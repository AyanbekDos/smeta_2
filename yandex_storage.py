"""
Yandex Object Storage адаптер для замены Google Cloud Storage
"""
import os
import boto3
from botocore.config import Config
import gzip
import json
from datetime import datetime
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

class YandexStorageClient:
    def __init__(self):
        self.access_key = os.getenv("YANDEX_ACCESS_KEY")
        self.secret_key = os.getenv("YANDEX_SECRET_KEY")  
        self.bucket_name = os.getenv("YANDEX_BUCKET")
        self.region = os.getenv("YANDEX_REGION", "ru-central1")
        
        if not all([self.access_key, self.secret_key, self.bucket_name]):
            logger.warning("Yandex Object Storage credentials not configured")
            self.client = None
            return
        
        # Создаем S3-совместимый клиент для Yandex Object Storage
        # Используем регион из переменной окружения (YANDEX_REGION),
        # т.к. бакет может находиться, например, в 'kz-central1'.
        # Явно укажем SigV4, чтобы избежать SignatureDoesNotMatch из-за версии подписи.
        self.client = boto3.client(
            's3',
            endpoint_url='https://storage.yandexcloud.net',
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
            config=Config(signature_version='s3v4', s3={'addressing_style': 'virtual'})
        )
        
        logger.info(f"Yandex Object Storage client initialized for bucket: {self.bucket_name} (region={self.region})")
    
    def upload_file(self, local_path: str, remote_path: str, content_type: str = None) -> bool:
        """Загружает файл в Yandex Object Storage"""
        if not self.client:
            logger.warning("Yandex Storage client not initialized")
            return False
            
        try:
            extra_args = {}
            if content_type:
                extra_args['ContentType'] = content_type
                
            self.client.upload_file(
                local_path, 
                self.bucket_name, 
                remote_path,
                ExtraArgs=extra_args
            )
            
            logger.info(f"Successfully uploaded {local_path} -> {remote_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to upload to Yandex Storage: {e}")
            return False
    
    def upload_string(self, content: str, remote_path: str, content_type: str = "text/plain") -> bool:
        """Загружает строку как файл в Yandex Object Storage"""
        if not self.client:
            logger.warning("Yandex Storage client not initialized") 
            return False
            
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=remote_path,
                Body=content.encode('utf-8'),
                ContentType=content_type
            )
            
            logger.info(f"Successfully uploaded string content -> {remote_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to upload string to Yandex Storage: {e}")
            return False
    
    def upload_json(self, data: Dict[Any, Any], remote_path: str) -> bool:
        """Загружает JSON данные в Yandex Object Storage"""
        try:
            json_content = json.dumps(data, ensure_ascii=False, indent=2)
            return self.upload_string(json_content, remote_path, "application/json")
        except Exception as e:
            logger.error(f"Failed to serialize JSON for upload: {e}")
            return False
    
    def upload_gzipped_string(self, content: str, remote_path: str, content_type: str = "text/plain") -> bool:
        """Загружает gzip-сжатую строку в Yandex Object Storage"""
        if not self.client:
            logger.warning("Yandex Storage client not initialized")
            return False
            
        try:
            compressed = gzip.compress(content.encode('utf-8'))
            
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=remote_path,
                Body=compressed,
                ContentType=content_type,
                ContentEncoding='gzip'
            )
            
            logger.info(f"Successfully uploaded gzipped content -> {remote_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to upload gzipped content to Yandex Storage: {e}")
            return False
    
    def download_file(self, remote_path: str, local_path: str) -> bool:
        """Скачивает файл из Yandex Object Storage"""
        if not self.client:
            logger.warning("Yandex Storage client not initialized")
            return False
            
        try:
            self.client.download_file(self.bucket_name, remote_path, local_path)
            logger.info(f"Successfully downloaded {remote_path} -> {local_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to download from Yandex Storage: {e}")
            return False
    
    def file_exists(self, remote_path: str) -> bool:
        """Проверяет существование файла в Yandex Object Storage"""
        if not self.client:
            return False
            
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=remote_path)
            return True
        except:
            return False
    
    def list_files(self, prefix: str = "") -> list:
        """Возвращает список файлов с префиксом"""
        if not self.client:
            return []
            
        try:
            response = self.client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix
            )
            
            files = []
            if 'Contents' in response:
                files = [obj['Key'] for obj in response['Contents']]
            
            return files
            
        except Exception as e:
            logger.error(f"Failed to list files: {e}")
            return []

# Глобальный экземпляр клиента
yandex_storage = YandexStorageClient()

def reinitialize_global_client():
    """Переинициализирует глобальный клиент с текущими переменными окружения."""
    global yandex_storage
    yandex_storage = YandexStorageClient()
