from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv
import os

load_dotenv()

user=os.getenv('DB_USER')
passw=os.getenv('DB_PASSWORD')
host=os.getenv('DB_HOST')
port=os.getenv('DB_PORT')
database=os.getenv('DB_NAME')

DATABASE_URL = 'postgresql://'+user+':'+passw+'@'+host+':'+port+'/'+database


def test_db_connection(db_url):
    """Test the database connection."""
    try:
        engine = create_engine(db_url)
        with engine.connect() as connection:
            print("✅ Database connection successful!")
    except SQLAlchemyError as e:
        print(f"❌ Database connection failed: {e}")
    finally:
        engine.dispose()

if __name__ == "__main__":
    test_db_connection(DATABASE_URL)
