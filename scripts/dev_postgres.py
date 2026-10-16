"""生成忽略的本地凭据并启动本项目 PostgreSQL；不输出密码或连接字符串。"""
import secrets
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    local = root / ".local"
    local.mkdir(exist_ok=True)
    credentials = local / "postgres.env"
    if not credentials.exists():
        password = secrets.token_urlsafe(32)
        credentials.write_text(f"POSTGRES_USER=wenjin\nPOSTGRES_DB=wenjin\nPOSTGRES_PASSWORD={password}\n", encoding="utf-8")
    values = dict(line.split("=", 1) for line in credentials.read_text(encoding="utf-8").splitlines() if "=" in line)
    config = local / "database.env"
    config.write_text(f"RSI_DATABASE_URL=postgresql+psycopg://wenjin:{values['POSTGRES_PASSWORD']}@127.0.0.1:55432/wenjin\n", encoding="utf-8")
    subprocess.run(["docker", "compose", "-f", "compose.dev.yml", "up", "-d", "--wait", "db"], cwd=root, check=True)
    print("PostgreSQL ready; connection configuration: .local/database.env")


if __name__ == "__main__":
    main()
