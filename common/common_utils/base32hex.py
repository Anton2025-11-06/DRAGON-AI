import base64


def b32hexencode(data: str) -> str:
    """
    base32hex 编码（字母表 0-9A-V，无大小写歧义，适合作为 token / 密码存储）
    如 b32hexencode("Admin@123") -> "85I6QQBE80OJ4CO="
    :param data: 待编码字符串
    :return: base32hex 编码字符串
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.b32hexencode(data).decode("utf-8")


def b32hexdecode(data: str) -> str:
    """
    base32hex 解码，自动补齐 '=' 填充位
    :param data: base32hex 编码字符串
    :return: 原始字符串
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    # base32 编码长度必须是 8 的倍数，缺位时补 '='
    padding = b"=" * (-len(data) % 8)
    return base64.b32hexdecode(data + padding).decode("utf-8")