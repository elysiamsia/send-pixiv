"""`no_sni` 里证书主机名校验的单元测试。

这部分是纯逻辑，能离线测；真正的握手行为在 bot 环境里实测过
（不发 SNI 连 210.140.139.133 能拿到 *.pximg.net 的证书并下到图）。
"""

from __future__ import annotations

import ssl
import unittest

from plugin_loader import load_package

load_package()

from send_pixiv_under_test.core.no_sni import (  # noqa: E402
    certificate_hosts,
    host_matches_cert,
    make_no_sni_context,
)

PXIMG_CERT = {
    "subjectAltName": (("DNS", "pximg.net"), ("DNS", "*.pximg.net")),
    "subject": ((("commonName", "pximg.net"),),),
}

PIXIV_CERT = {
    "subjectAltName": (
        ("DNS", "pixiv.net"),
        ("DNS", "*.pixiv.net"),
        ("DNS", "oauth.secure.pixiv.net"),
        ("DNS", "fanbox.cc"),
        ("DNS", "*.fanbox.cc"),
    ),
}


class CertificateHostsTest(unittest.TestCase):
    """`certificate_hosts` 的用例。"""

    def test_reads_san(self) -> None:
        self.assertEqual(certificate_hosts(PXIMG_CERT), ["pximg.net", "*.pximg.net"])

    def test_falls_back_to_cn(self) -> None:
        self.assertEqual(certificate_hosts({"subject": ((("commonName", "a.example"),),)}), ["a.example"])

    def test_empty_cert(self) -> None:
        self.assertEqual(certificate_hosts({}), [])


class HostMatchesCertTest(unittest.TestCase):
    """`host_matches_cert` 的用例。"""

    def test_exact(self) -> None:
        self.assertTrue(host_matches_cert("pximg.net", PXIMG_CERT))

    def test_wildcard_single_label(self) -> None:
        self.assertTrue(host_matches_cert("i.pximg.net", PXIMG_CERT))
        self.assertTrue(host_matches_cert("s.pximg.net", PXIMG_CERT))

    def test_wildcard_does_not_cross_labels(self) -> None:
        "\*.pximg.net 不该匹配 a.b.pximg.net（RFC 6125）。"
        self.assertFalse(host_matches_cert("a.b.pximg.net", PXIMG_CERT))

    def test_unrelated_host_rejected(self) -> None:
        self.assertFalse(host_matches_cert("evil.com", PXIMG_CERT))
        self.assertFalse(host_matches_cert("pximg.net.evil.com", PXIMG_CERT))
        self.assertFalse(host_matches_cert("evilpximg.net", PXIMG_CERT))

    def test_case_and_trailing_dot(self) -> None:
        self.assertTrue(host_matches_cert("I.PXIMG.NET", PXIMG_CERT))
        self.assertTrue(host_matches_cert("i.pximg.net.", PXIMG_CERT))

    def test_empty_host(self) -> None:
        self.assertFalse(host_matches_cert("", PXIMG_CERT))

    def test_real_pixiv_cert(self) -> None:
        "实测拿到的 pixiv 证书样本。"
        self.assertTrue(host_matches_cert("app-api.pixiv.net", PIXIV_CERT))
        self.assertTrue(host_matches_cert("oauth.secure.pixiv.net", PIXIV_CERT))
        self.assertTrue(host_matches_cert("fanbox.cc", PIXIV_CERT))
        self.assertFalse(host_matches_cert("i.pximg.net", PIXIV_CERT))


class MakeContextTest(unittest.TestCase):
    """`make_no_sni_context` 的用例。"""

    def test_chain_verification_stays_on(self) -> None:
        ctx = make_no_sni_context("i.pximg.net")
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED, "证书链必须继续校验")

    def test_hostname_check_is_manual(self) -> None:
        "TLS 层的主机名校验关掉（没 SNI 就没得比），由我们按 SAN 补上。"
        ctx = make_no_sni_context("i.pximg.net")
        self.assertFalse(ctx.check_hostname)

    def test_wrap_methods_are_overridden(self) -> None:
        ctx = make_no_sni_context("i.pximg.net")
        self.assertIsNot(ctx.wrap_bio, ssl.SSLContext.wrap_bio)
        self.assertIsNot(ctx.wrap_socket, ssl.SSLContext.wrap_socket)


if __name__ == "__main__":
    unittest.main()
