import json
import urllib.error
import urllib.request


from tests._pytest_port import BASE, deployment_settings


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read()), r.status


def get_raw(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return r.read().decode(), r.status


def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


DIRECTORY_SUBTITLE = "Sign in with your employee ID and password"


def _deployment_language(language):
    """The login page is served before login, so it speaks the Deployment's
    language; a User's own language choice belongs to their Profile (ADR 0006)."""
    return deployment_settings(language=language)


def test_login_page_uses_simplified_chinese_for_zh_cn_alias():
    with _deployment_language("zh-CN"):
        html, status2 = get_raw("/login")
        assert status2 == 200
        assert 'lang="zh-CN"' in html
        assert "\u767b\u5f55" in html
        # GFIT-CoWork: the subtitle is the Directory copy, English until the
        # other locales are translated (phase 2).
        assert DIRECTORY_SUBTITLE in html


def test_login_page_uses_traditional_chinese_for_zh_hant():
    with _deployment_language("zh-Hant"):
        html, status2 = get_raw("/login")
        assert status2 == 200
        assert 'lang="zh-TW"' in html
        # GFIT-CoWork: the subtitle is the Directory copy, English until the
        # other locales are translated (phase 2).
        assert DIRECTORY_SUBTITLE in html
        assert "\u5bc6\u78bc\u932f\u8aa4" in html


def test_login_page_uses_russian_for_ru():
    with _deployment_language("ru"):
        html, status2 = get_raw("/login")
        assert status2 == 200
        assert 'lang="ru-RU"' in html
        assert "\u0412\u043e\u0439\u0442\u0438" in html
        # GFIT-CoWork: the subtitle is the Directory copy, English until the
        # other locales are translated (phase 2).
        assert DIRECTORY_SUBTITLE in html
        assert "\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u043f\u0430\u0440\u043e\u043b\u044c" in html
