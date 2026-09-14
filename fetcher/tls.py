"""tpex.org.tw 的 TLS 驗證：自備 TWCA 中繼憑證。

2026-09-07 櫃買換了新憑證之後，伺服器只送 leaf、不送中繼（TWCA SSL Certification
Authority），瀏覽器會自己走 AIA 抓中繼所以看起來正常，Python/OpenSSL 不會 →
`CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`，本機和 GitHub
runner 都炸。解法不是 verify=False，是把中繼憑證併進 certifi 的 bundle 再驗：
中繼由 certifi 內建的 TWCA CYBER Root CA 簽，鏈就完整了。

中繼憑證來源：leaf 的 AIA caIssuers http://sslserver.twca.com.tw/cacert/Cyber_SSL_2023.crt
（有效期到 2033-02），存在 data/twca_ssl_ca.pem。
"""
from pathlib import Path

import certifi

_INTER = Path(__file__).parent.parent / "data" / "twca_ssl_ca.pem"
_BUNDLE = Path(__file__).parent.parent / ".cache" / "tpex_bundle.pem"


def tpex_bundle() -> str:
    """certifi + TWCA 中繼，合併檔快取在 .cache/（certifi 或中繼有更新就重做）。"""
    src_mtime = max(Path(certifi.where()).stat().st_mtime, _INTER.stat().st_mtime)
    if not _BUNDLE.exists() or _BUNDLE.stat().st_mtime < src_mtime:
        _BUNDLE.parent.mkdir(exist_ok=True)
        _BUNDLE.write_bytes(Path(certifi.where()).read_bytes() + b"\n" + _INTER.read_bytes())
    return str(_BUNDLE)


def verify_for(url: str):
    """給 requests 的 verify 參數：tpex 走自備 bundle，其他 host 照常。"""
    return tpex_bundle() if "tpex.org.tw" in url else True
