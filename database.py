
import os
from pathlib import Path

import mysql.connector
from mysql.connector import Error
from config import Config


def get_db_connection():
    try:
        # Check database environment variables
        if not Config.DB_HOST:
            raise ValueError("DB_HOST is missing")

        if not Config.DB_USER:
            raise ValueError("DB_USER is missing")

        if not Config.DB_PASSWORD:
            raise ValueError("DB_PASSWORD is missing")

        if not Config.DB_NAME:
            raise ValueError("DB_NAME is missing")

        # Resolve SSL certificate path for Render and local Windows
        base_dir = Path(__file__).resolve().parent

        ca_setting = os.getenv("DB_SSL_CA") or Config.DB_SSL_CA

        if not ca_setting:
            raise ValueError("DB_SSL_CA is missing")

        ca_path = Path(ca_setting)

        if not ca_path.is_absolute():
            ca_path = base_dir / ca_path

        ca_path = ca_path.resolve()

        if not ca_path.is_file():
            raise FileNotFoundError(
                f"SSL certificate not found: {ca_path}"
            )

        print("Connecting to TiDB Cloud...")
        print("SSL certificate found:", ca_path)

        connection = mysql.connector.connect(
            host=Config.DB_HOST,
            port=Config.DB_PORT,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            database=Config.DB_NAME,
            ssl_ca=str(ca_path),
            ssl_verify_cert=True,
            ssl_verify_identity=True,
            connection_timeout=20
        )

        if connection.is_connected():
            print("SUCCESS: TiDB Cloud connected!")
            return connection

        print("Connection was not established.")
        return None

    except Exception as e:
        print("DATABASE CONNECTION ERROR:", repr(e))
        return None


def create_tables():
    connection = get_db_connection()

    if not connection:
        print("Database connection failed!")
        return False

    cursor = connection.cursor()

    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(150) NOT NULL UNIQUE,
                phone VARCHAR(15) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'student',
                usn VARCHAR(30) UNIQUE,
                semester INT DEFAULT 5,
                department VARCHAR(50) DEFAULT 'CSE',
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                submission_date DATE NOT NULL,
                image_filename VARCHAR(255) NOT NULL,
                image_path VARCHAR(500) NOT NULL,
                image_hash VARCHAR(64) NOT NULL UNIQUE,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_user_submission_day
                    (user_id, submission_date)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_reports (
                id INT AUTO_INCREMENT PRIMARY KEY,
                report_date DATE NOT NULL UNIQUE,
                uploaded_count INT DEFAULT 0,
                not_uploaded_count INT DEFAULT 0,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                excel_path VARCHAR(500),
                zip_path VARCHAR(500)
            )
        """)

        connection.commit()
        print("SUCCESS: Database tables created!")
        return True

    except Error as e:
        connection.rollback()
        print("TABLE CREATION ERROR:", repr(e))
        return False

    finally:
        cursor.close()
        connection.close()


if __name__ == "__main__":
    connection = get_db_connection()

    if connection:
        connection.close()
        print("SUCCESS: TiDB Cloud Database Connected!")
        create_tables()
    else:
        print("FAILED: Database connection failed!")
