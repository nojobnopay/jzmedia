"""Server-local recovery: python -m app.reset_protection_password.

No password command-line arguments or environment variables are accepted.
Content choices and devices in children mode are preserved.
"""
import getpass


def main():
    from . import store, viewing
    store.init_db()
    password = getpass.getpass("新的保护密码（6–128 个字符）：")
    if password != getpass.getpass("再次输入："):
        raise SystemExit("两次密码不一致，未修改")
    if not 6 <= len(password) <= 128:
        raise SystemExit("密码长度不符合要求，未修改")
    viewing.set_password(password)
    print("保护密码已重设；隐私内容保留，所有解锁授权已撤销。")


if __name__ == "__main__":
    main()
