from pathlib import Path
import pandas as pd
import time
import mysql.connector
import re
import hashlib
import json
import math
from datetime import date, datetime
from decimal import Decimal
import logging
from dotenv import load_dotenv
import os

load_dotenv()

def make_record_hash(row):
    normalized = []

    for value in row:
        if value is None or pd.isna(value):
            normalized.append(None)

        elif isinstance(value, (datetime, date, pd.Timestamp)):
            normalized.append(value.isoformat())

        elif isinstance(value, (int, float, Decimal)):
            number = float(value)

            if not math.isfinite(number):
                normalized.append(None)
            else:
                normalized.append(format(Decimal(str(value)).normalize(), "f"))

        else:
            normalized.append(str(value).strip())

    content = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":")
    )

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


logging.basicConfig(
    filename="automation.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)


folder = Path(r"C:\Users\Muneesh\Desktop\Python Automation\input")
def get_mysql_connection():
    connection = mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "insightiq_db")
    )

    return connection

def normalize_value(value):
    """Normalize values so CSV rows can be compared with MySQL rows."""

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime().isoformat(sep=" ")

    if hasattr(value, "item"):
        value = value.item()

    if isinstance(value, float):
        return round(value, 6)

    if hasattr(value, "isoformat"):
        try:
            return value.isoformat(sep=" ")
        except TypeError:
            return value.isoformat()

    return str(value)



def load_to_mysql(data, table_name):
    if data.empty:
        print("\nNo records to process.")
        return


    table_name = re.sub(r"\W+", "_", table_name).strip("_").lower()

    if not table_name:
        raise ValueError("Invalid table name")

    if table_name[0].isdigit():
        table_name = "table_" + table_name

    connection = get_mysql_connection()
    cursor = connection.cursor()

    try:

        mysql_types = []

        for column in data.columns:
            dtype = data[column].dtype

            if pd.api.types.is_integer_dtype(dtype):
                mysql_type = "BIGINT"
            elif pd.api.types.is_float_dtype(dtype):
                mysql_type = "DOUBLE"
            elif pd.api.types.is_datetime64_any_dtype(dtype):
                mysql_type = "DATETIME"
            else:
                mysql_type = "TEXT"

            mysql_types.append(f"`{column}` {mysql_type}")

        create_query = f"""
            CREATE TABLE IF NOT EXISTS `{table_name}` (
                {", ".join(mysql_types)}
            )
        """
        cursor.execute(create_query)


        cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
        existing_columns = {row[0] for row in cursor.fetchall()}

        for column in data.columns:
            if column not in existing_columns:
                dtype = data[column].dtype

                if pd.api.types.is_integer_dtype(dtype):
                    mysql_type = "BIGINT"
                elif pd.api.types.is_float_dtype(dtype):
                    mysql_type = "DOUBLE"
                elif pd.api.types.is_datetime64_any_dtype(dtype):
                    mysql_type = "DATETIME"
                else:
                    mysql_type = "TEXT"

                cursor.execute(
                    f"ALTER TABLE `{table_name}` "
                    f"ADD COLUMN `{column}` {mysql_type}"
                )


        id_candidates = [
            col for col in data.columns
            if (
                col == "id"
                or col.endswith("_id")
                or col in {"uuid", "record_id"}
            )
        ]

        key_column = None

        for column in id_candidates:
            values = data[column]

            if (
                values.notna().all()
                and values.is_unique
            ):
                key_column = column
                break

        columns = list(data.columns)
        quoted_columns = ", ".join(f"`{c}`" for c in columns)
        placeholders = ", ".join(["%s"] * len(columns))

        insert_query = f"""
            INSERT INTO `{table_name}` ({quoted_columns})
            VALUES ({placeholders})
        """


        cursor.execute(f"SELECT {quoted_columns} FROM `{table_name}`")
        existing_rows = cursor.fetchall()

        inserted = 0
        updated = 0
        unchanged = 0

        if key_column:
            key_index = columns.index(key_column)
            existing_by_key = {}

            for row in existing_rows:
                key = normalize_value(row[key_index])

                if key in existing_by_key:
                    raise ValueError(
                        f"Duplicate ID {key!r} already exists in "
                        f"table '{table_name}'. Resolve it before syncing."
                    )

                existing_by_key[key] = row

            print(f"\nMatching records using ID column: {key_column}")

            for incoming in data.itertuples(index=False, name=None):
                row = tuple(
                    None if pd.isna(v)
                    else v.to_pydatetime() if isinstance(v, pd.Timestamp)
                    else v.item() if hasattr(v, "item")
                    else v
                    for v in incoming
                )

                key = normalize_value(row[key_index])

                if key in existing_by_key:
                    old_row = existing_by_key[key]

                    changed_columns = [
                        i for i, col in enumerate(columns)
                        if col != key_column
                        and normalize_value(row[i])
                        != normalize_value(old_row[i])
                    ]

                    if not changed_columns:
                        unchanged += 1
                        continue

                    set_clause = ", ".join(
                        f"`{columns[i]}` = %s"
                        for i in changed_columns
                    )

                    update_query = (
                        f"UPDATE `{table_name}` SET {set_clause} "
                        f"WHERE `{key_column}` = %s"
                    )

                    params = tuple(
                        row[i] for i in changed_columns
                    ) + (row[key_index],)

                    cursor.execute(update_query, params)
                    updated += 1

                    new_old_row = list(old_row)
                    for i in changed_columns:
                        new_old_row[i] = row[i]

                    existing_by_key[key] = tuple(new_old_row)

                else:
                    cursor.execute(insert_query, row)
                    inserted += 1
                    existing_by_key[key] = row

        else:
            print(
                "\nNo suitable unique ID found. "
                "Using exact-row matching; changed rows cannot "
                "reliably be linked to their previous records."
            )

            existing_set = {
                tuple(normalize_value(v) for v in row)
                for row in existing_rows
            }

            for incoming in data.itertuples(index=False, name=None):
                row = tuple(
                    None if pd.isna(v)
                    else v.to_pydatetime() if isinstance(v, pd.Timestamp)
                    else v.item() if hasattr(v, "item")
                    else v
                    for v in incoming
                )

                normalized = tuple(normalize_value(v) for v in row)

                if normalized in existing_set:
                    unchanged += 1
                    continue

                cursor.execute(insert_query, row)
                existing_set.add(normalized)
                inserted += 1

        connection.commit()


        logging.info(
            "Sync completed | Table: %s | Inserted: %d | Updated: %d | Skipped: %d",
            table_name, inserted, updated, unchanged
        )

        print(f"\nNew records inserted: {inserted}")
        print(f"Existing records updated: {updated}")
        print(f"Unchanged records skipped: {unchanged}")
        print("No records deleted.")


    except Exception:
        connection.rollback()
        raise

    finally:
        cursor.close()
        connection.close()

last_processed_file = None
last_processed_time = None

connection = get_mysql_connection()

if connection.is_connected():
    print("\nMySQL connected successfully!")

connection.close()
while True:


    csv_files = list(folder.glob("*.csv"))

    if not csv_files:
        print("\nNo CSV file found")
        time.sleep(10)
        continue

    latest_file = max(
        csv_files,
        key=lambda file: file.stat().st_mtime
    )

    latest_time = latest_file.stat().st_mtime


    if (
        latest_file != last_processed_file
        or latest_time != last_processed_time
    ):

        print("\nNew/updated file detected:", latest_file.name)


        size1 = latest_file.stat().st_size

        time.sleep(5)

        size2 = latest_file.stat().st_size

        if size1 == size2:

            print("\nFile is stable")

           

            def standardize_column_names(columns):
                cleaned = []
                used = set()

                for index, column in enumerate(columns, start=1):
                    name = str(column).strip().lower()
                    name = re.sub(r"\W+", "_", name)
                    name = name.strip("_")

                    if not name:
                        name = f"column_{index}"

                    if name[0].isdigit():
                        name = f"col_{name}"

                    original = name
                    suffix = 2

                    while name in used:
                        name = f"{original}_{suffix}"
                        suffix += 1

                    used.add(name)
                    cleaned.append(name)

                return cleaned
            def clean_data(data):


                data.columns = standardize_column_names(data.columns)

                data.dropna(how="all", inplace=True)

                before_duplicates = len(data)

                data.drop_duplicates(inplace=True)

                after_duplicates = len(data)

                print("\nDuplicates removed:", before_duplicates - after_duplicates)


                text_columns = data.select_dtypes(include=["object"]).columns

                for column in text_columns:
                    data[column] = data[column].str.strip()

                id_candidates = [
                    col for col in data.columns
                    if col == "id"
                    or col.endswith("_id")
                    or col in {"uuid", "record_id"}
                ]

                for column in id_candidates:
                    if data[column].isna().any():
                        raise ValueError(
                            f"ID column '{column}' contains missing values."
                        )

                    if data[column].duplicated().any():
                        raise ValueError(
                            f"ID column '{column}' contains duplicate IDs."
                        )

                print("\nID validation passed.")

                print("\nMissing values:")
                print(data.isnull().sum())

                for column in data.columns:

                    if data[column].isnull().sum() > 0:

                        if data[column].dtype in ["int64", "float64"]:

                            data[column] = data[column].fillna(data[column].median())

                        else:

                            data[column] = data[column].fillna("Unknown")
 


                if data.empty:
                    raise ValueError("Dataset is empty after cleaning!")

                print("\nData validation passed.")


                print("\nRows:", len(data))
                print("Columns:", len(data.columns))
                print("Duplicate rows:", data.duplicated().sum())
                print("Missing values:", data.isnull().sum().sum())
                print(data.columns.tolist())
                
                return data
            
             
#           Read CSV

            try:
                data = pd.read_csv(
                    latest_file,
                    engine="python"
                )
            except (pd.errors.ParserError, UnicodeDecodeError, OSError) as e:
                print(f"\nERROR: CSV file could not be read: {e}")
                time.sleep(10)
                continue


            print("\nBefore cleaning:")
            print(data.shape)

            data = clean_data(data)
            table_name = latest_file.stem.lower().replace(" ", "_")


            try:
                load_to_mysql(data, table_name)

            except Exception as e:
                print(f"\nPROCESSING FAILED: {e}")
                print("File will be retried after the next check.")

            else:
                print("\nAfter cleaning:")
                print(data.shape)

                print("\nCleaned data:")
                print(data.head())

                output_folder = Path(r"C:\Users\Muneesh\Desktop\Python Automation\output")
                output_folder.mkdir(parents=True, exist_ok=True)

                cleaned_file = output_folder / f"cleaned_{latest_file.name}"
                data.to_csv(cleaned_file, index=False)

                print("\nCleaned file saved:")
                print(cleaned_file)

                last_processed_file = latest_file
                last_processed_time = latest_time

        else:
            print("\nFile is still changing. Will check again.")

    else:
        print("No new file.")

    time.sleep(10)