# CSV to MySQL Automation

A Python-based data automation project that monitors a folder for CSV files, cleans the data using Pandas, and synchronizes records with a MySQL database.

## Features

- Detects new or modified CSV files.
- Standardizes column names and removes duplicate rows.
- Handles missing values and validates ID columns.
- Creates database tables and adds new columns when needed.
- Inserts new records, updates changed records, and skips unchanged records.
- Preserves existing database records even if they are removed from the source CSV.
- Logs successful synchronization activity.

## Tech Stack

- Python
- Pandas
- MySQL
- mysql-connector-python

## Project Structure

```text
_pandas data/
├── find_files.py
├── requirements.txt
├── README.md
├── .gitignore
├── input/
└── output/
```

## Setup

1. Install Python and MySQL.
2. Install dependencies:

   `pip install -r requirements.txt`

3. Create a MySQL database named `insightiq_db`.
4. Configure the MySQL connection in `find_files.py` according to your local setup.
5. Place a CSV file in the configured input folder.
6. Run:

   `python find_files.py`

## Notes

This project is designed for local CSV-to-MySQL synchronization. Review database credentials and test with sample data before using it with important datasets.