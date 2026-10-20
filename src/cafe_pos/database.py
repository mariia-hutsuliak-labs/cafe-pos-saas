import logging

from sqlalchemy import create_engine
from sqlalchemy.schema import CreateIndex
from sqlalchemy.orm import sessionmaker, declarative_base

from cafe_pos.config import settings

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


logger = logging.getLogger("cafe_pos")


def create_missing_indexes():
    """Додає індекси, яких ще немає в базі.

    Base.metadata.create_all створює індекси тільки разом з новою таблицею.
    Якщо таблиця вже була в базі, індекси з моделей треба додати окремо.
    CREATE INDEX IF NOT EXISTS безпечно викликати при кожному старті.
    """
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            try:
                with engine.begin() as connection:
                    connection.execute(CreateIndex(index, if_not_exists=True))
            except Exception:
                # Репліки стартують одночасно: інша могла створити індекс першою.
                logger.warning("Не вдалося створити індекс %s", index.name)
