"""Create the local Langfuse credentials once, with owner-only permissions."""

import os
from pathlib import Path
from secrets import token_hex


def main():
    path = Path(__file__).resolve().parents[1] / ".env.langfuse"
    values = {
        "LF_POSTGRES_PASSWORD": token_hex(16),
        "LF_CLICKHOUSE_PASSWORD": token_hex(16),
        "LF_STORAGE_PASSWORD": token_hex(16),
        "LF_REDIS_PASSWORD": token_hex(16),
        "LF_SALT": token_hex(16),
        "LF_ENCRYPTION_KEY": token_hex(32),
        "LF_NEXTAUTH_SECRET": token_hex(32),
        "LANGFUSE_PUBLIC_KEY": f"pk-lf-{token_hex(16)}",
        "LANGFUSE_SECRET_KEY": f"sk-lf-{token_hex(16)}",
        "LANGFUSE_INIT_USER_EMAIL": "admin@example.test",
        "LANGFUSE_INIT_USER_PASSWORD": token_hex(16),
    }
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"Using existing {path.name}")
        return

    with os.fdopen(fd, "w", encoding="utf-8") as config:
        config.writelines(f"{name}={value}\n" for name, value in values.items())
    print(f"Created {path.name}")


if __name__ == "__main__":
    main()
