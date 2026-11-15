"""クラウド定期実行に必要な環境変数を、値を出さずに検査する。"""
import os
import sys


def main():
    required = ("SUPABASE_URL", "SUPABASE_KEY", "KEIBABOOK_LOGIN_ID", "KEIBABOOK_PASSWORD")
    missing = [key for key in required if not os.environ.get(key, "").strip()]
    if missing:
        print("GitHub Actions Secretsに未設定の項目があります: " + ", ".join(missing))
        return 1
    if os.environ.get("KEIBA_FORCE_SQLITE"):
        print("クラウド更新ではKEIBA_FORCE_SQLITEを指定できません")
        return 1
    print("クラウド用の認証設定が揃っています（値は非表示）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
