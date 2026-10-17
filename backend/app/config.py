from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='../.env', extra='ignore')
    mysql_database: str = 'comgraf'
    mysql_user: str = 'comgraf'
    mysql_password: str = ''
    db_host: str = '127.0.0.1'
    db_port: int = 3306
    app_timezone: str = 'America/Tegucigalpa'
    cookie_secure: bool = False
    upload_dir: str = '../uploads'
    static_dir: str = '../frontend/dist'
    session_hours: int = 12
    product_version: str = '1.2.0'
    build_id: str = 'local-1.2.0'

    @property
    def database_url(self):
        return URL.create('mysql+pymysql', username=self.mysql_user,
                          password=self.mysql_password, host=self.db_host,
                          port=self.db_port, database=self.mysql_database,
                          query={'charset': 'utf8mb4'})


settings = Settings()
