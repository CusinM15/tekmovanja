"""
Application settings and configuration.
"""

import os
from dataclasses import dataclass
from typing import Optional
import json
from pathlib import Path

# Default configuration
DEFAULT_CONFIG = {
    "smtp": {
        "host": "smtp.arne.gov.si",  # Example Arnes SMTP host
        "port": 587,
        "user": None,
        "password": None,
        "tls": True,
    },
    "database": {
        "sqlite_path": ":memory:",  # Default to in-memory; can be set to file path
    },
    "schools": {
        # Predefined schools with email addresses
        "OŠ Žirovnica": "irovnica@arne.gov.si",
        "OŠ Prežihov Voranc": "voranc@arne.gov.si",
        "OŠ Koroška Bela": "koroska_bela@arne.gov.si",
        "OŠ Josipa Vandota Kranjska Gora": "kranjska_gora@arne.gov.si",
        "OŠ 16. decembra Mojstrana": "mojstrana@arne.gov.si",
    },
    "disciplines": {
        # Disciplines that start with these schools by default
        "tek 60m M": ["2.r", "3.r"],
        "tek 60m Ž": ["2.r", "3.r"],
        "tek 400m M": ["2.r", "3.r"],
        "tek 400m ž": ["2.r", "3.r"],
        "skok v daljino M": ["2.r", "3.r"],
        "skok v daljino Ž": ["2.r", "3.r"],
        "met vorteksa M": ["2.r", "3.r"],
        "met vorteksa Ž": ["2.r", "3.r"],
    },
}


@dataclass
class AppConfig:
    """Application configuration."""
    
    # Database
    sqlite_path: Optional[str] = None
    
    # SMTP settings
    smtp_host: str = "smtp.arne.gov.si"
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_tls: bool = True
    
    # Schools
    default_schools: dict = None
    
    # Disciplines
    default_disciplines: dict = None
    
    # Paths
    app_data_dir: Path = Path(os.getenv("APPDATA", "/tmp")) / "tekmovanja"
    
    def __post_init__(self):
        if self.default_schools is None:
            self.default_schools = DEFAULT_CONFIG["schools"]
        if self.default_disciplines is None:
            self.default_disciplines = DEFAULT_CONFIG["disciplines"]
        
        # Create app data directory
        self.app_data_dir.mkdir(parents=True, exist_ok=True)
        
        # Load or create config file
        self.config_file = self.app_data_dir / "config.json"
        self.load_or_create_config()
    
    def load_or_create_config(self):
        """Load config from file or create defaults."""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                # Merge with defaults and set overrides
                config = DEFAULT_CONFIG.copy()
                for key, value in data.items():
                    config[key] = value
                
                # Override with current instance values
                config["smtp"] = {**config.get("smtp", {}), **self._get_smtp_dict()}
                # school settings merge logic
                # discipline settings merge logic
                # We'll keep it simple for now
                
            except (json.JSONDecodeError, IOError) as e:
                print(f"Config file error, using defaults: {e}")
                self._save_config(DEFAULT_CONFIG)
                return
        else:
            # Create default config file
            config = {**DEFAULT_CONFIG}
            self._set_smtp_in_config(config, self._get_smtp_dict())
            self._save_config(config)
        pass
    
    def _get_smtp_dict(self):
        """Return SMTP settings as dict."""
        return {
            "host": self.smtp_host,
            "port": self.smtp_port,
            "user": self.smtp_user,
            "password": self.smtp_password,
            "tls": self.smtp_tls,
        }
    
    def _set_smtp_in_config(self, config, smtp_dict):
        """Set SMTP settings in config dict."""
        config["smtp"] = smtp_dict
    
    def _save_config(self, config):
        """Save config to file."""
        # Normalize config structure
        smtp = config.get("smtp", {})
        # Ensure host, port, user, password, tls are present
        # Save as JSON
        full_config = {
            "smtp": smtp,
            "database": config.get("database", {}),
            "schools": config.get("schools", {}),
            "disciplines": config.get("disciplines", {}),
        }
        
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(full_config, f, indent=2, ensure_ascii=False)
    
    def get_smtp_settings(self) -> dict:
        """Get SMTP settings for sending email."""
        return {
            "host": self.smtp_host,
            "port": self.smtp_port,
            "user": self.smtp_user,
            "password": self.smtp_password,
            "tls": self.smtp_tls,
        }
    
    def set_smtp_credentials(self, user: str, password: str):
        """Set SMTP credentials. Validates they are provided."""
        if not user or not password:
            raise ValueError("SMTP username and password must be provided")
        self.smtp_user = user
        self.smtp_password = password
        self._save_config(self._full_config())
    
    def clear_smtp_credentials(self):
        """Clear SMTP credentials."""
        self.smtp_user = None
        self.smtp_password = None
        self._save_config(self._full_config())
    
    def _full_config(self):
        """Return full config dict."""
        return {
            "smtp": self._get_smtp_dict(),
            "database": {"sqlite_path": self.sqlite_path},
            "schools": self.default_schools,
            "disciplines": self.default_disciplines,
        }

def get_app_config() -> AppConfig:
    """Get the application configuration instance (lazy initialization)."""
    # The AppConfig instance is created on first access
    if not hasattr(get_app_config, "_instance"):
        app_config = AppConfig()
        get_app_config._instance = app_config
    return get_app_config._instance

# Lazy access function - returned value will be used by API endpoints
app_config = get_app_config()
