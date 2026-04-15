#!/usr/bin/env python3
"""Convert a Feishu cloud document link into Markdown for Care-Dev knowledge columns.

The script preserves common structures such as headings, lists, tables, links,
code blocks, and images. When upload settings are provided, extracted images are
uploaded through the same Care-Dev admin file upload flow used by the web UI so
the generated Markdown contains stable RustFS URLs.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import http.client
import hmac
import json
import mimetypes
import os
import posixpath
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import xml.etree.ElementTree as ET

try:
    import browser_cookie3  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    browser_cookie3 = None

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    requests = None


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
HEADING_PATTERN = re.compile(r"(heading|标题)\s*([1-6])", re.IGNORECASE)
FEISHU_URL_PATTERN = re.compile(r"^https?://[^\s]+/(?:wiki|docx|docs)/[A-Za-z0-9_-]+", re.IGNORECASE)
FEISHU_WIKI_URL_PATTERN = re.compile(r"https?://[^\s]+/wiki/([A-Za-z0-9_-]+)", re.IGNORECASE)
FEISHU_DOCX_URL_PATTERN = re.compile(r"https?://[^\s]+/(?:docx|docs)/([A-Za-z0-9_-]+)", re.IGNORECASE)
FEISHU_HEADING_KEY_PATTERN = re.compile(r"heading([1-9])$")
FEISHU_CLIENT_VARS_PATTERN = re.compile(
    r"window\.DATA = Object\.assign\(\{\}, window\.DATA, \{ clientVars: Object\((.*?)\) \}\);",
    re.S,
)
CODE_STYLE_KEYWORDS = (
    "code",
    "代码",
    "代码块",
    "source",
    "pre",
    "program",
    "quote code",
)
MONOSPACE_FONTS = ("consolas", "menlo", "monaco", "courier", "fira code")
FEISHU_BLOCK_DATA_KEYS = (
    "page",
    "text",
    "heading1",
    "heading2",
    "heading3",
    "heading4",
    "heading5",
    "heading6",
    "heading7",
    "heading8",
    "heading9",
    "bullet",
    "ordered",
    "code",
    "quote",
    "todo",
    "callout",
    "divider",
    "file",
    "image",
    "table",
    "table_cell",
    "quote_container",
    "grid",
    "grid_column",
    "iframe",
    "sheet",
    "bitable",
    "diagram",
    "chat_card",
    "view",
    "mindnote",
    "isv",
    "add_ons",
    "undefined",
)
FEISHU_CODE_LANGUAGE_MAP = {
    7: "bash",
    60: "markdown",
}


def local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def read_xml(docx: zipfile.ZipFile, path: str) -> Optional[ET.Element]:
    try:
        return ET.fromstring(docx.read(path))
    except KeyError:
        return None


def text_attr(element: Optional[ET.Element], attr: str = f"{{{NS['w']}}}val") -> str:
    if element is None:
        return ""
    return element.attrib.get(attr, "")


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_public_url(raw_url: str) -> str:
    raw_url = raw_url.strip()
    if not raw_url:
        return ""
    parsed = urllib.parse.urlsplit(raw_url)
    normalized = ""
    if parsed.scheme and parsed.netloc:
        normalized = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    else:
        normalized = raw_url.split("?", 1)[0].split("#", 1)[0]
    return apply_public_url_overrides(normalized)


def decode_url(raw_value: str) -> str:
    return urllib.parse.unquote(raw_value or "")


def apply_public_url_overrides(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    rewrite_from = os.getenv("CARE_DOCX_URL_REWRITE_FROM", "").strip()
    rewrite_to = os.getenv("CARE_DOCX_URL_REWRITE_TO", "").strip()
    if rewrite_from and rewrite_to and url.startswith(rewrite_from):
        url = f"{rewrite_to.rstrip('/')}{url[len(rewrite_from.rstrip('/')):]}"
    if parse_bool(os.getenv("CARE_DOCX_FORCE_HTTPS", "false")) and url.startswith("http://"):
        url = f"https://{url[len('http://'):]}"
    return url


def render_sheet_text_style(text: str, style: Optional[Dict[str, Any]]) -> str:
    if not text:
        return ""
    style = style or {}
    if style.get("strikeThrough"):
        text = f"~~{text}~~"
    if style.get("bold") and style.get("italic"):
        text = f"***{text}***"
    elif style.get("bold"):
        text = f"**{text}**"
    elif style.get("italic"):
        text = f"*{text}*"
    if style.get("underline"):
        text = f"<u>{text}</u>"
    return text


def flatten_sheet_cell_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(flatten_sheet_cell_value(item) for item in value)
    if isinstance(value, dict):
        text = value.get("text", "")
        if not text and isinstance(value.get("texts"), list):
            text = "".join(flatten_sheet_cell_value(item) for item in value.get("texts") or [])
        if text:
            text = render_sheet_text_style(text, value.get("segmentStyle"))
            link = decode_url(str(value.get("link", "") or ""))
            return f"[{text}]({link})" if link else text
        if "link" in value and value.get("link"):
            link = decode_url(str(value.get("link", "") or ""))
            return link
        for candidate in ("name", "label", "title", "content", "value"):
            candidate_value = value.get(candidate)
            if isinstance(candidate_value, str) and candidate_value.strip():
                return candidate_value
        return ""
    return str(value)


def escape_markdown_table_text(value: Any) -> str:
    text = flatten_sheet_cell_value(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = text.replace("\n", "<br>")
    return text.replace("|", r"\|")


def trim_sheet_rows(rows: List[List[Any]]) -> List[List[Any]]:
    normalized = [list(row) for row in rows if isinstance(row, list)]
    while normalized and not any(flatten_sheet_cell_value(cell).strip() for cell in normalized[-1]):
        normalized.pop()
    if not normalized:
        return []
    max_columns = max((len(row) for row in normalized), default=0)
    while max_columns > 0:
        if any(
            flatten_sheet_cell_value(row[max_columns - 1]).strip()
            for row in normalized
            if len(row) >= max_columns
        ):
            break
        max_columns -= 1
    if max_columns <= 0:
        return []
    return [(row + [""] * (max_columns - len(row)))[:max_columns] for row in normalized]


def markdown_table_from_rows(rows: List[List[Any]]) -> List[str]:
    trimmed_rows = trim_sheet_rows(rows)
    if not trimmed_rows:
        return []
    header = [escape_markdown_table_text(cell) or " " for cell in trimmed_rows[0]]
    column_size = len(header)
    separator = ["---"] * column_size
    lines = [
        f"| {' | '.join(header)} |",
        f"| {' | '.join(separator)} |",
    ]
    for row in trimmed_rows[1:]:
        normalized_row = [
            escape_markdown_table_text(row[idx] if idx < len(row) else "")
            for idx in range(column_size)
        ]
        lines.append(f"| {' | '.join(normalized_row)} |")
    return lines


def split_feishu_sheet_token(raw_token: str) -> Tuple[str, str]:
    spreadsheet_token, separator, sheet_id = raw_token.strip().partition("_")
    if not spreadsheet_token or not separator or not sheet_id:
        raise RuntimeError(f"Unsupported Feishu sheet token: {raw_token}")
    return spreadsheet_token, sheet_id


def normalize_upload_endpoint(base_url: str) -> str:
    base_url = base_url.strip()
    if not base_url:
        raise ValueError("Base URL cannot be empty")
    parsed = urllib.parse.urlsplit(base_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/infra/file/upload"):
        final_path = path
    elif path.endswith("/admin-api"):
        final_path = f"{path}/infra/file/upload"
    elif "/admin-api/" in path:
        final_path = f"{path}/infra/file/upload"
    else:
        final_path = f"{path}/admin-api/infra/file/upload"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, final_path, "", ""))


def guess_content_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def guess_extension_from_content_type(content_type: str) -> str:
    return mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip()) or ".bin"


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = strip_wrapping_quotes(value.strip())


def is_feishu_url(value: str) -> bool:
    return bool(FEISHU_URL_PATTERN.match(value.strip()))


def default_cookie_domain_for_url(value: str) -> str:
    host = (urllib.parse.urlsplit(value).hostname or "").strip().lower()
    if host.endswith(".feishu.cn") or host == "feishu.cn":
        return "feishu.cn"
    if host.endswith(".larksuite.com") or host == "larksuite.com":
        return "larksuite.com"
    return host


def load_browser_cookie_session(url: str, browser: str = "chrome", cookie_domain: str = "") -> Any:
    if browser_cookie3 is None:
        raise RuntimeError("browser_cookie3 is not installed; cannot load browser cookies")
    if requests is None:
        raise RuntimeError("requests is not installed; cannot fetch Feishu pages with browser cookies")

    browser_name = (browser or "chrome").strip().lower()
    loader = getattr(browser_cookie3, browser_name, None)
    if loader is None:
        raise RuntimeError(f"Unsupported browser cookie source: {browser_name}")

    domain_name = cookie_domain.strip() or default_cookie_domain_for_url(url)
    cookie_jar = loader(domain_name=domain_name) if domain_name else loader()
    session = requests.Session()
    session.cookies.update(cookie_jar)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36"
            )
        }
    )
    return session


def fetch_feishu_html_with_browser_cookies(
    url: str,
    browser: str = "chrome",
    cookie_domain: str = "",
) -> Tuple[Any, str]:
    session = load_browser_cookie_session(url, browser=browser, cookie_domain=cookie_domain)
    response = session.get(url, allow_redirects=True, timeout=(10, 30))
    response.raise_for_status()
    return session, response.text


def extract_feishu_client_vars_from_html(html_text: str) -> Dict[str, Any]:
    match = FEISHU_CLIENT_VARS_PATTERN.search(html_text)
    if not match:
        raise RuntimeError("Could not find window.DATA.clientVars in the Feishu page HTML")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Failed to parse embedded Feishu clientVars JSON from HTML") from exc


def flatten_feishu_attributed_text(text_payload: Any) -> str:
    if not isinstance(text_payload, dict):
        return ""
    initial = text_payload.get("initialAttributedTexts") or {}
    text_map = initial.get("text")
    if isinstance(text_map, dict):
        def sort_key(value: Any) -> Tuple[int, str]:
            text = str(value)
            return (0, text) if text.isdigit() else (1, text)

        pieces = [text_map[key] for key in sorted(text_map, key=sort_key)]
        return "".join(piece for piece in pieces if isinstance(piece, str))
    if isinstance(text_map, list):
        return "".join(piece for piece in text_map if isinstance(piece, str))
    return ""


def make_feishu_text_elements(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    return [{"text_run": {"content": text, "text_element_style": {}}}]


def html_block_payload_to_api_block(block_id: str, raw_block: Dict[str, Any]) -> Dict[str, Any]:
    data = raw_block.get("data", {}) or {}
    block_type = str(data.get("type") or "undefined").strip() or "undefined"
    children = data.get("children", []) or []
    block: Dict[str, Any] = {
        "block_id": block_id,
        "parent_id": data.get("parent_id", ""),
        "children": children,
    }
    if block_type == "page":
        block["block_type"] = 1

    if block_type in {
        "page",
        "text",
        "heading1",
        "heading2",
        "heading3",
        "heading4",
        "heading5",
        "heading6",
        "heading7",
        "heading8",
        "heading9",
        "bullet",
        "ordered",
        "quote",
        "todo",
        "callout",
    }:
        payload = dict(data.get(block_type, {}) or {})
        if "elements" not in payload:
            payload["elements"] = make_feishu_text_elements(flatten_feishu_attributed_text(data.get("text")))
        block[block_type] = payload
        return block

    if block_type == "code":
        payload = dict(data.get("code", {}) or {})
        if "elements" not in payload:
            payload["elements"] = make_feishu_text_elements(flatten_feishu_attributed_text(data.get("text")))
        block["code"] = payload
        return block

    payload = data.get(block_type)
    if isinstance(payload, dict):
        block[block_type] = payload
    elif payload is not None:
        block[block_type] = payload
    else:
        block[block_type] = {}
    return block


def build_feishu_cover_url(token: str, block_id: str, width: Any = 1280, height: Any = 1280) -> str:
    params = urllib.parse.urlencode(
        {
            "fallback_source": 1,
            "height": height or 1280,
            "mount_node_token": block_id,
            "mount_point": "docx_image",
            "policy": "equal",
            "width": width or 1280,
        }
    )
    return (
        "https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/v2/cover/"
        f"{urllib.parse.quote(token)}/?{params}"
    )


def parse_feishu_html_source(html_text: str) -> Tuple[str, List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    client_vars = extract_feishu_client_vars_from_html(html_text)
    data = client_vars.get("data", {}) or {}
    block_map = data.get("block_map", {}) or {}
    meta_map = data.get("meta_map", {}) or {}
    title = find_first_string(meta_map, ("title",)) or "feishu-document"

    blocks: List[Dict[str, Any]] = []
    media_sources: Dict[str, Dict[str, Any]] = {}
    for block_id, raw_block in block_map.items():
        if not isinstance(raw_block, dict):
            continue
        api_block = html_block_payload_to_api_block(block_id, raw_block)
        blocks.append(api_block)

        data_payload = raw_block.get("data", {}) or {}
        if data_payload.get("type") == "image":
            image = data_payload.get("image", {}) or {}
            token = image.get("token", "")
            if token:
                media_sources[token] = {
                    "url": build_feishu_cover_url(
                        token=token,
                        block_id=block_id,
                        width=image.get("width") or 1280,
                        height=image.get("height") or 1280,
                    ),
                    "name": image.get("name") or "image.png",
                }
    return title, blocks, media_sources


def find_first_string(node: Any, candidate_fields: Sequence[str]) -> str:
    if isinstance(node, dict):
        for field in candidate_fields:
            value = node.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for child in node.values():
            value = find_first_string(child, candidate_fields)
            if value:
                return value
    elif isinstance(node, list):
        for child in node:
            value = find_first_string(child, candidate_fields)
            if value:
                return value
    return ""


def load_relationships(docx: zipfile.ZipFile, path: str) -> Dict[str, str]:
    root = read_xml(docx, path)
    if root is None:
        return {}
    rels: Dict[str, str] = {}
    for rel in root:
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if rel_id and target:
            rels[rel_id] = target
    return rels


def load_style_map(docx: zipfile.ZipFile) -> Dict[str, str]:
    root = read_xml(docx, "word/styles.xml")
    if root is None:
        return {}
    styles: Dict[str, str] = {}
    for style in root.findall("w:style", NS):
        style_id = style.attrib.get(f"{{{NS['w']}}}styleId", "")
        name = style.find("w:name", NS)
        styles[style_id] = text_attr(name) or style_id
    return styles


def load_numbering(docx: zipfile.ZipFile) -> Tuple[Dict[str, str], Dict[str, Dict[int, str]]]:
    root = read_xml(docx, "word/numbering.xml")
    if root is None:
        return {}, {}

    abstract_formats: Dict[str, Dict[int, str]] = {}
    for abstract_num in root.findall("w:abstractNum", NS):
        abstract_id = abstract_num.attrib.get(f"{{{NS['w']}}}abstractNumId", "")
        levels: Dict[int, str] = {}
        for level in abstract_num.findall("w:lvl", NS):
            ilvl = level.attrib.get(f"{{{NS['w']}}}ilvl", "0")
            fmt = text_attr(level.find("w:numFmt", NS)) or "bullet"
            levels[int(ilvl)] = fmt
        if abstract_id:
            abstract_formats[abstract_id] = levels

    num_to_abstract: Dict[str, str] = {}
    for num in root.findall("w:num", NS):
        num_id = num.attrib.get(f"{{{NS['w']}}}numId", "")
        abstract = num.find("w:abstractNumId", NS)
        abstract_id = text_attr(abstract)
        if num_id and abstract_id:
            num_to_abstract[num_id] = abstract_id

    return num_to_abstract, abstract_formats


class UploadClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        directory: str,
        tenant_id: Optional[str] = None,
        visit_tenant_id: Optional[str] = None,
    ) -> None:
        self.endpoint = normalize_upload_endpoint(base_url)
        if token and not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        self.token = token
        self.directory = directory
        self.tenant_id = tenant_id
        self.visit_tenant_id = visit_tenant_id

    def upload(self, filename: str, payload: bytes) -> str:
        boundary = f"----care-docx-{uuid.uuid4().hex}"
        body = bytearray()

        def add_field(name: str, value: str) -> None:
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
            )
            body.extend(value.encode("utf-8"))
            body.extend(b"\r\n")

        def add_file(name: str, file_name: str, data: bytes) -> None:
            content_type = guess_content_type(file_name)
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{file_name}"\r\n'
                ).encode("utf-8")
            )
            body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
            body.extend(data)
            body.extend(b"\r\n")

        add_field("directory", self.directory)
        add_file("file", filename, payload)
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = self.token
        if self.tenant_id:
            headers["tenant-id"] = self.tenant_id
        if self.visit_tenant_id:
            headers["visit-tenant-id"] = self.visit_tenant_id

        request = urllib.request.Request(
            self.endpoint,
            data=bytes(body),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request) as response:
                payload_text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Upload failed with HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Upload request failed: {exc.reason}") from exc

        try:
            result = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Upload returned non-JSON response: {payload_text}") from exc

        code = result.get("code")
        if code not in (0, "0", None):
            raise RuntimeError(result.get("msg") or f"Upload failed with code {code}")

        data = result.get("data")
        if not isinstance(data, str) or not data.strip():
            raise RuntimeError(f"Upload response missing URL: {payload_text}")
        return normalize_public_url(data)


class RustFSUploadClient:
    def __init__(
        self,
        endpoint: str,
        domain: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str,
        directory: str,
        path_style: bool = True,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.domain = domain.rstrip("/")
        self.bucket = bucket.strip()
        self.access_key = access_key.strip()
        self.secret_key = secret_key.strip()
        self.region = region.strip() or "us-east-1"
        self.directory = (directory or "").strip().strip("/")
        self.path_style = path_style
        self.verify_upload = parse_bool(os.getenv("CARE_DOCX_RUSTFS_VERIFY", "true"), True)
        self.max_retries = max(1, int(os.getenv("CARE_DOCX_RUSTFS_RETRIES", "3")))
        self.rename_on_failure = parse_bool(
            os.getenv("CARE_DOCX_RUSTFS_RENAME_ON_FAILURE", "true"),
            True,
        )

    def upload(self, filename: str, payload: bytes) -> str:
        object_key = self.build_object_key(filename)
        try:
            self.upload_with_retries(object_key, payload, guess_content_type(filename))
            return normalize_public_url(self.build_public_url(object_key))
        except Exception as primary_error:
            if not self.rename_on_failure:
                raise
            fallback_filename = self.build_retry_filename(filename)
            fallback_key = self.build_object_key(fallback_filename)
            self.upload_with_retries(
                fallback_key,
                payload,
                guess_content_type(fallback_filename),
            )
            print(
            f"[feishu->markdown] warning=rustfs_key_fallback from={object_key} to={fallback_key}",
                file=sys.stderr,
            )
            return normalize_public_url(self.build_public_url(fallback_key))

    def upload_with_retries(self, object_key: str, payload: bytes, content_type: str) -> None:
        target_url = self.build_target_url(object_key)
        for attempt in range(1, self.max_retries + 1):
            try:
                self.put_object(target_url, payload, content_type)
                if self.verify_upload:
                    self.verify_object(target_url, payload)
                break
            except Exception as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        f"RustFS upload verification failed after {attempt} attempts: {exc}"
                    ) from exc
                time.sleep(min(0.5 * attempt, 2.0))

    def build_object_key(self, filename: str) -> str:
        return f"{self.directory}/{filename}" if self.directory else filename

    def build_retry_filename(self, filename: str) -> str:
        path = Path(filename)
        suffix = path.suffix
        stem = path.stem or "asset"
        return f"{stem}-retry-{uuid.uuid4().hex[:8]}{suffix}"

    def build_target_url(self, object_key: str) -> str:
        parsed = urllib.parse.urlsplit(self.endpoint)
        encoded_key = urllib.parse.quote(object_key, safe="/")
        if self.path_style:
            path = f"{parsed.path.rstrip('/')}/{urllib.parse.quote(self.bucket)}/{encoded_key}"
            return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        host = parsed.hostname or ""
        netloc = f"{self.bucket}.{host}"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urllib.parse.urlunsplit((parsed.scheme, netloc, f"/{encoded_key}", "", ""))

    def build_public_url(self, object_key: str) -> str:
        encoded_key = urllib.parse.quote(object_key, safe="/")
        if self.domain:
            return f"{self.domain}/{encoded_key}"
        return self.build_target_url(object_key)

    def put_object(self, target_url: str, payload: bytes, content_type: str) -> None:
        body_hash = hashlib.sha256(payload).hexdigest()
        now = __import__("datetime").datetime.utcnow()
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        parsed = urllib.parse.urlsplit(target_url)
        host = parsed.netloc
        headers = {
            "host": host,
            "x-amz-content-sha256": body_hash,
            "x-amz-date": amz_date,
            "content-type": content_type,
            "content-length": str(len(payload)),
        }
        canonical_headers = (
            f"host:{headers['host']}\n"
            f"x-amz-content-sha256:{headers['x-amz-content-sha256']}\n"
            f"x-amz-date:{headers['x-amz-date']}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            [
                "PUT",
                urllib.parse.quote(parsed.path or "/", safe="/~"),
                parsed.query,
                canonical_headers,
                signed_headers,
                body_hash,
            ]
        )
        credential_scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )

        def sign(key: bytes, message: str) -> bytes:
            return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

        k_date = sign(("AWS4" + self.secret_key).encode("utf-8"), date_stamp)
        k_region = sign(k_date, self.region)
        k_service = sign(k_region, "s3")
        k_signing = sign(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        request = urllib.request.Request(target_url, data=payload, headers=headers, method="PUT")
        try:
            with urllib.request.urlopen(request):
                return
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"RustFS upload failed with HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"RustFS upload failed: {exc.reason}") from exc

    def verify_object(self, target_url: str, payload: bytes) -> None:
        request = urllib.request.Request(target_url, method="GET")
        try:
            with urllib.request.urlopen(request) as response:
                downloaded = response.read()
        except http.client.IncompleteRead as exc:
            partial = exc.partial or b""
            raise RuntimeError(
                f"RustFS returned an incomplete object body: got {len(partial)} of {len(payload)} bytes"
            ) from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"RustFS verification GET failed with HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"RustFS verification GET failed: {exc.reason}") from exc

        if len(downloaded) != len(payload):
            raise RuntimeError(
                f"RustFS returned {len(downloaded)} bytes, expected {len(payload)} bytes"
            )
        if hashlib.sha256(downloaded).digest() != hashlib.sha256(payload).digest():
            raise RuntimeError("RustFS verification hash mismatch after upload")


class ImageStore:
    def __init__(
        self,
        docx: zipfile.ZipFile,
        relationships: Dict[str, str],
        document_stem: str,
        upload_client: Optional[UploadClient],
        assets_dir: Optional[Path],
    ) -> None:
        self.docx = docx
        self.relationships = relationships
        self.document_stem = sanitize_filename(document_stem)
        self.upload_client = upload_client
        self.assets_dir = assets_dir
        self.cache: Dict[str, str] = {}
        self.asset_name_usage: Dict[str, int] = defaultdict(int)
        if self.assets_dir:
            self.assets_dir.mkdir(parents=True, exist_ok=True)

    def markdown_for_relation(self, rel_id: str) -> str:
        if not rel_id:
            return ""
        if rel_id in self.cache:
            return f"![image]({self.cache[rel_id]})"
        target = self.relationships.get(rel_id)
        if not target:
            return ""
        media_path = posixpath.normpath(posixpath.join("word", target))
        data = self.docx.read(media_path)
        generated_name = self.generated_name(Path(target).suffix or ".bin")
        if self.upload_client:
            url = self.upload_client.upload(generated_name, data)
        else:
            if self.assets_dir is None:
                raise RuntimeError("assets_dir must be set when upload is disabled")
            output_path = self.assets_dir / generated_name
            output_path.write_bytes(data)
            url = output_path.as_posix()
        self.cache[rel_id] = url
        return f"![image]({url})"

    def generated_name(self, suffix: str) -> str:
        clean_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        base = f"{self.document_stem}-image"
        self.asset_name_usage[base] += 1
        return f"{base}-{self.asset_name_usage[base]:03d}{clean_suffix}"


class DownloadedAssetStore:
    def __init__(
        self,
        feishu_client: "FeishuClient",
        document_stem: str,
        upload_client: Optional[UploadClient],
        assets_dir: Optional[Path],
    ) -> None:
        self.feishu_client = feishu_client
        self.document_stem = sanitize_filename(document_stem)
        self.upload_client = upload_client
        self.assets_dir = assets_dir
        self.cache: Dict[str, str] = {}
        self.asset_name_usage: Dict[str, int] = defaultdict(int)
        if self.assets_dir:
            self.assets_dir.mkdir(parents=True, exist_ok=True)

    def resolve(self, token: str, suggested_name: str) -> str:
        if not token:
            return ""
        if token in self.cache:
            return self.cache[token]
        download_name, payload = self.feishu_client.download_media(token, suggested_name)
        generated_name = self.generated_name(download_name or suggested_name or token)
        if self.upload_client:
            url = self.upload_client.upload(generated_name, payload)
        else:
            if self.assets_dir is None:
                raise RuntimeError("assets_dir must be set when upload is disabled")
            output_path = self.assets_dir / generated_name
            output_path.write_bytes(payload)
            url = output_path.as_posix()
        self.cache[token] = url
        return url

    def generated_name(self, suggested_name: str) -> str:
        path = Path(suggested_name or "asset.bin")
        stem = sanitize_filename(path.stem or "asset")
        suffix = path.suffix or ".bin"
        base = stem if stem.startswith(self.document_stem) else f"{self.document_stem}-{stem}"
        self.asset_name_usage[base] += 1
        return f"{base}-{self.asset_name_usage[base]:03d}{suffix}"


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or "document"


def paragraph_style_name(paragraph: ET.Element, style_map: Dict[str, str]) -> str:
    p_pr = paragraph.find("w:pPr", NS)
    style = p_pr.find("w:pStyle", NS) if p_pr is not None else None
    style_id = text_attr(style)
    if not style_id:
        return ""
    return style_map.get(style_id, style_id)


def heading_level(style_name: str) -> Optional[int]:
    if not style_name:
        return None
    match = HEADING_PATTERN.search(style_name)
    if match:
        return int(match.group(2))
    if style_name.isdigit() and 1 <= int(style_name) <= 6:
        return int(style_name)
    return None


def paragraph_num_info(paragraph: ET.Element) -> Tuple[Optional[str], int]:
    p_pr = paragraph.find("w:pPr", NS)
    if p_pr is None:
        return None, 0
    num_pr = p_pr.find("w:numPr", NS)
    if num_pr is None:
        return None, 0
    num_id = text_attr(num_pr.find("w:numId", NS)) or None
    ilvl_raw = text_attr(num_pr.find("w:ilvl", NS)) or "0"
    try:
        ilvl = int(ilvl_raw)
    except ValueError:
        ilvl = 0
    return num_id, ilvl


def run_has_style_flag(run: ET.Element, tag_name: str) -> bool:
    r_pr = run.find("w:rPr", NS)
    if r_pr is None:
        return False
    return r_pr.find(f"w:{tag_name}", NS) is not None


def run_font_names(run: ET.Element) -> List[str]:
    r_pr = run.find("w:rPr", NS)
    if r_pr is None:
        return []
    r_fonts = r_pr.find("w:rFonts", NS)
    if r_fonts is None:
        return []
    values = []
    for attr_name in ("ascii", "hAnsi", "cs"):
        value = r_fonts.attrib.get(f"{{{NS['w']}}}{attr_name}")
        if value:
            values.append(value.lower())
    return values


def is_code_paragraph(paragraph: ET.Element, style_name: str) -> bool:
    lowered = style_name.lower()
    if any(keyword in lowered for keyword in CODE_STYLE_KEYWORDS):
        return True
    for run in paragraph.findall(".//w:r", NS):
        fonts = run_font_names(run)
        if any(font.startswith(prefix) for font in fonts for prefix in MONOSPACE_FONTS):
            return True
    return False


def wrap_inline(text: str, bold: bool, italic: bool, strike: bool) -> str:
    if not text:
        return ""
    if strike:
        text = f"~~{text}~~"
    if bold and italic:
        return f"***{text}***"
    if bold:
        return f"**{text}**"
    if italic:
        return f"*{text}*"
    return text


def convert_run(run: ET.Element, image_store: ImageStore) -> str:
    parts: List[str] = []
    bold = run_has_style_flag(run, "b") or run_has_style_flag(run, "bCs")
    italic = run_has_style_flag(run, "i") or run_has_style_flag(run, "iCs")
    strike = run_has_style_flag(run, "strike")

    for child in run:
        name = local_name(child.tag)
        if name == "t":
            text = child.text or ""
            if child.attrib.get(XML_SPACE) != "preserve":
                text = text
            parts.append(wrap_inline(text, bold, italic, strike))
        elif name in ("br", "cr"):
            parts.append("\n")
        elif name == "tab":
            parts.append("    ")
        elif name == "drawing":
            for blip in child.findall(".//a:blip", NS):
                parts.append(image_store.markdown_for_relation(blip.attrib.get(f"{{{NS['r']}}}embed", "")))
        elif name == "pict":
            for image_data in child.findall(".//v:imagedata", NS):
                parts.append(
                    image_store.markdown_for_relation(
                        image_data.attrib.get(f"{{{NS['r']}}}id", "")
                    )
                )
        elif name == "noBreakHyphen":
            parts.append("-")
        elif name == "sym":
            char = child.attrib.get(f"{{{NS['w']}}}char", "")
            if char:
                try:
                    parts.append(chr(int(char, 16)))
                except ValueError:
                    pass
    return "".join(parts)


def convert_hyperlink(
    hyperlink: ET.Element,
    relationships: Dict[str, str],
    image_store: ImageStore,
) -> str:
    chunks = [convert_run(run, image_store) for run in hyperlink.findall("w:r", NS)]
    text = "".join(chunks)
    rel_id = hyperlink.attrib.get(f"{{{NS['r']}}}id", "")
    target = relationships.get(rel_id, "")
    if target and text and "![" not in text:
        return f"[{text}]({target})"
    return text


def paragraph_inline_markdown(
    paragraph: ET.Element,
    relationships: Dict[str, str],
    image_store: ImageStore,
) -> str:
    parts: List[str] = []
    for child in paragraph:
        name = local_name(child.tag)
        if name == "r":
            parts.append(convert_run(child, image_store))
        elif name == "hyperlink":
            parts.append(convert_hyperlink(child, relationships, image_store))
    text = "".join(parts)
    text = text.replace("\u00a0", " ")
    return text


def cell_markdown(
    cell: ET.Element,
    relationships: Dict[str, str],
    image_store: ImageStore,
    style_map: Dict[str, str],
) -> str:
    paragraphs = []
    for paragraph in cell.findall("w:p", NS):
        style_name = paragraph_style_name(paragraph, style_map)
        text = paragraph_inline_markdown(paragraph, relationships, image_store).strip()
        if is_code_paragraph(paragraph, style_name) and text:
            paragraphs.append(f"`{text}`")
        elif text:
            paragraphs.append(text)
    merged = "<br>".join(paragraphs)
    merged = merged.replace("|", r"\|")
    return merged


def table_markdown(
    table: ET.Element,
    relationships: Dict[str, str],
    image_store: ImageStore,
    style_map: Dict[str, str],
) -> List[str]:
    rows: List[List[str]] = []
    for row in table.findall("w:tr", NS):
        cells = [
            cell_markdown(cell, relationships, image_store, style_map)
            for cell in row.findall("w:tc", NS)
        ]
        rows.append(cells)
    if not rows:
        return []
    column_count = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (column_count - len(row)) for row in rows]
    header = normalized_rows[0]
    separator = ["---"] * column_count
    lines = [
        f"| {' | '.join(header)} |",
        f"| {' | '.join(separator)} |",
    ]
    for row in normalized_rows[1:]:
        lines.append(f"| {' | '.join(row)} |")
    return lines


def list_prefix(
    num_id: str,
    ilvl: int,
    numbering_map: Dict[str, str],
    numbering_levels: Dict[str, Dict[int, str]],
    counters: Dict[str, List[int]],
) -> str:
    abstract_id = numbering_map.get(num_id)
    fmt = "bullet"
    if abstract_id:
        fmt = numbering_levels.get(abstract_id, {}).get(ilvl, "bullet")
    indent = "  " * max(ilvl, 0)
    if fmt == "bullet":
        return f"{indent}- "
    levels = counters.setdefault(num_id, [])
    while len(levels) <= ilvl:
        levels.append(0)
    levels[ilvl] += 1
    for index in range(ilvl + 1, len(levels)):
        levels[index] = 0
    return f"{indent}{levels[ilvl]}. "


def finalize_markdown(lines: Iterable[str]) -> str:
    output: List[str] = []
    previous_blank = False
    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            if previous_blank:
                continue
            previous_blank = True
            output.append("")
            continue
        previous_blank = False
        if output and output[-1] and stripped.startswith("#"):
            output.append("")
        if output and output[-1] and stripped.startswith("```"):
            output.append("")
        if output and output[-1] and stripped.startswith("|") and not output[-1].startswith("|"):
            output.append("")
        output.append(stripped)
    return "\n".join(output).strip() + "\n"


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, base_url: str = "https://open.feishu.cn") -> None:
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
        self.base_url = base_url.rstrip("/")
        self._tenant_access_token: Optional[str] = None

    def get_tenant_access_token(self) -> str:
        if self._tenant_access_token:
            return self._tenant_access_token
        response = self.request_json(
            "POST",
            "/open-apis/auth/v3/tenant_access_token/internal",
            payload={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        token = response.get("tenant_access_token") or response.get("data", {}).get("tenant_access_token", "")
        if not token:
            raise RuntimeError("Feishu tenant_access_token response was empty")
        self._tenant_access_token = token
        return token

    def resolve_doc_token(self, source_url: str) -> str:
        tenant_token = self.get_tenant_access_token()
        wiki_match = FEISHU_WIKI_URL_PATTERN.search(source_url)
        if wiki_match:
            wiki_token = wiki_match.group(1)
            response = self.request_json(
                "GET",
                f"/open-apis/wiki/v2/spaces/get_node?token={urllib.parse.quote(wiki_token)}",
                token=tenant_token,
            )
            node = response.get("data", {}).get("node", {})
            obj_type = (node.get("obj_type") or "").lower()
            obj_token = node.get("obj_token") or ""
            if obj_type != "docx" or not obj_token:
                raise RuntimeError("The Feishu wiki link did not resolve to a docx document")
            return obj_token

        docx_match = FEISHU_DOCX_URL_PATTERN.search(source_url)
        if docx_match:
            return docx_match.group(1)

        raise RuntimeError("Unsupported Feishu link. Expected /wiki/ or /docx/ URL")

    def get_document_title(self, document_token: str) -> str:
        tenant_token = self.get_tenant_access_token()
        response = self.request_json(
            "GET",
            f"/open-apis/docx/v1/documents/{urllib.parse.quote(document_token)}",
            token=tenant_token,
        )
        title = find_first_string(response, ("title", "document_title", "name"))
        return title or document_token

    def load_all_blocks(self, document_token: str) -> List[Dict[str, Any]]:
        tenant_token = self.get_tenant_access_token()
        blocks: List[Dict[str, Any]] = []
        page_token = ""
        while True:
            query = [("page_size", "500")]
            if page_token:
                query.append(("page_token", page_token))
            path = (
                f"/open-apis/docx/v1/documents/{urllib.parse.quote(document_token)}/blocks?"
                + urllib.parse.urlencode(query)
            )
            response = self.request_json("GET", path, token=tenant_token)
            data = response.get("data", {})
            blocks.extend(data.get("items", []) or [])
            if not data.get("has_more"):
                break
            page_token = data.get("page_token") or ""
            if not page_token:
                break
        return blocks

    def load_sheet_rows(self, raw_token: str) -> List[List[Any]]:
        spreadsheet_token, sheet_id = split_feishu_sheet_token(raw_token)
        tenant_token = self.get_tenant_access_token()
        range_ref = urllib.parse.quote(sheet_id, safe="!:$")
        response = self.request_json(
            "GET",
            f"/open-apis/sheets/v2/spreadsheets/{urllib.parse.quote(spreadsheet_token)}/values/{range_ref}",
            token=tenant_token,
        )
        data = response.get("data", {}) or {}
        value_range = data.get("valueRange") or data.get("value_range") or {}
        values = value_range.get("values") or data.get("values") or []
        if not isinstance(values, list):
            return []
        return [list(row) if isinstance(row, list) else [row] for row in values]

    def download_media(self, file_token: str, suggested_name: str = "") -> Tuple[str, bytes]:
        tenant_token = self.get_tenant_access_token()
        payload, headers = self.request_binary(
            "GET",
            f"/open-apis/drive/v1/medias/{urllib.parse.quote(file_token)}/download",
            token=tenant_token,
        )
        filename = extract_filename_from_headers(headers)
        if not filename:
            extension = guess_extension_from_content_type(headers.get_content_type())
            base_name = sanitize_filename(Path(suggested_name or file_token).stem or file_token)
            filename = f"{base_name}{extension}"
        return filename, payload

    def request_json(
        self,
        method: str,
        path: str,
        token: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        headers = {"Accept": "application/json"}
        data: Optional[bytes] = None
        if token:
            headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        if payload is not None:
            headers["Content-Type"] = "application/json; charset=UTF-8"
            data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            self.build_url(path),
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request) as response:
                payload_text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Feishu API request failed with HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Feishu API request failed: {exc.reason}") from exc

        try:
            result = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Feishu API returned non-JSON response: {payload_text}") from exc

        code = result.get("code")
        if code not in (0, "0", None):
            raise RuntimeError(result.get("msg") or f"Feishu API returned code {code}")
        return result

    def request_binary(self, method: str, path: str, token: str) -> Tuple[bytes, Any]:
        headers = {"Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}"}
        request = urllib.request.Request(self.build_url(path), headers=headers, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                return response.read(), response.headers
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Feishu binary request failed with HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Feishu binary request failed: {exc.reason}") from exc

    def build_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}{path}"


class FeishuHtmlSessionClient:
    def __init__(self, session: Any, media_sources: Dict[str, Dict[str, Any]]) -> None:
        self.session = session
        self.media_sources = media_sources

    def load_sheet_rows(self, raw_token: str) -> List[List[Any]]:
        raise RuntimeError(f"sheet_not_supported_in_html_fallback:{raw_token}")

    def download_media(self, file_token: str, suggested_name: str = "") -> Tuple[str, bytes]:
        source = self.media_sources.get(file_token)
        if not source:
            raise RuntimeError(f"HTML fallback could not resolve media token: {file_token}")
        if self.session is None:
            raise RuntimeError("HTML fallback needs a browser-cookie session to download media")
        response = self.session.get(source["url"], allow_redirects=True, timeout=(10, 30))
        response.raise_for_status()
        filename = suggested_name or source.get("name") or file_token
        return filename, bytes(response.content)


def extract_filename_from_headers(headers: Any) -> str:
    content_disposition = headers.get("Content-Disposition", "")
    utf8_match = re.search(r"filename\\*=UTF-8''([^;]+)", content_disposition, re.IGNORECASE)
    if utf8_match:
        return decode_url(utf8_match.group(1))
    plain_match = re.search(r'filename="?([^";]+)"?', content_disposition, re.IGNORECASE)
    if plain_match:
        return plain_match.group(1)
    return ""


def convert_docx(
    input_path: Path,
    output_path: Path,
    assets_dir: Optional[Path],
    upload_client: Optional[UploadClient],
) -> Tuple[int, int]:
    with zipfile.ZipFile(input_path) as docx:
        document = read_xml(docx, "word/document.xml")
        if document is None:
            raise RuntimeError("The DOCX file does not contain word/document.xml")

        body = document.find("w:body", NS)
        if body is None:
            raise RuntimeError("The DOCX file is missing the document body")

        doc_rels = load_relationships(docx, "word/_rels/document.xml.rels")
        style_map = load_style_map(docx)
        numbering_map, numbering_levels = load_numbering(docx)
        image_store = ImageStore(
            docx=docx,
            relationships=doc_rels,
            document_stem=input_path.stem,
            upload_client=upload_client,
            assets_dir=assets_dir,
        )

        lines: List[str] = []
        code_buffer: List[str] = []
        counters: Dict[str, List[int]] = {}
        image_count = 0

        def flush_code() -> None:
            if not code_buffer:
                return
            lines.append("```")
            lines.extend(code_buffer)
            lines.append("```")
            lines.append("")
            code_buffer.clear()

        for block in body:
            name = local_name(block.tag)
            if name == "tbl":
                flush_code()
                lines.extend(table_markdown(block, doc_rels, image_store, style_map))
                lines.append("")
                continue
            if name != "p":
                continue

            style_name = paragraph_style_name(block, style_map)
            inline = paragraph_inline_markdown(block, doc_rels, image_store)
            image_count += inline.count("![image](")
            inline = inline.replace("\n", "<br>")
            inline = normalize_space(inline) if "![" not in inline else inline.strip()
            heading = heading_level(style_name)

            if is_code_paragraph(block, style_name):
                code_text = paragraph_inline_markdown(block, doc_rels, image_store).rstrip()
                if code_text:
                    code_buffer.extend(code_text.splitlines() or [""])
                else:
                    code_buffer.append("")
                continue

            flush_code()

            if heading and inline:
                lines.append(f"{'#' * heading} {inline}")
                lines.append("")
                continue

            num_id, ilvl = paragraph_num_info(block)
            if num_id and inline:
                lines.append(f"{list_prefix(num_id, ilvl, numbering_map, numbering_levels, counters)}{inline}")
                continue

            if inline:
                lines.append(inline)
                lines.append("")
            else:
                lines.append("")

        flush_code()
        output_path.write_text(finalize_markdown(lines), encoding="utf-8")
        return len(image_store.cache), image_count


def feishu_block_key(block: Dict[str, Any]) -> str:
    for key in FEISHU_BLOCK_DATA_KEYS:
        if key in block:
            return key
    return ""


def render_feishu_text_style(text: str, style: Optional[Dict[str, Any]]) -> str:
    if not text:
        return ""
    style = style or {}
    formatted = text
    if style.get("inline_code"):
        formatted = f"`{formatted}`"
    if style.get("strikethrough"):
        formatted = f"~~{formatted}~~"
    if style.get("bold") and style.get("italic"):
        formatted = f"***{formatted}***"
    elif style.get("bold"):
        formatted = f"**{formatted}**"
    elif style.get("italic"):
        formatted = f"*{formatted}*"
    if style.get("underline"):
        formatted = f"<u>{formatted}</u>"
    link = decode_url(style.get("link", {}).get("url", ""))
    if link:
        formatted = f"[{formatted}]({link})"
    return formatted


def render_feishu_text_elements(elements: Optional[List[Dict[str, Any]]]) -> str:
    parts: List[str] = []
    for element in elements or []:
        if "text_run" in element:
            text_run = element["text_run"] or {}
            parts.append(
                render_feishu_text_style(
                    text_run.get("content", ""),
                    text_run.get("text_element_style"),
                )
            )
            continue
        if "mention_doc" in element:
            mention = element["mention_doc"] or {}
            label = mention.get("token") or "文档"
            text = render_feishu_text_style(label, mention.get("text_element_style"))
            url = decode_url(mention.get("url", ""))
            parts.append(f"[{text}]({url})" if url else text)
            continue
        if "mention_user" in element:
            mention = element["mention_user"] or {}
            label = f"@{mention.get('user_id', 'user')}"
            parts.append(render_feishu_text_style(label, mention.get("text_element_style")))
            continue
        if "equation" in element:
            equation = element["equation"] or {}
            content = equation.get("content", "")
            if content:
                parts.append(f"${content}$")
            continue
        if "reminder" in element:
            reminder = element["reminder"] or {}
            parts.append(render_feishu_text_style("提醒", reminder.get("text_element_style")))
            continue
        if "file" in element:
            inline_file = element["file"] or {}
            label = inline_file.get("file_token", "附件")
            parts.append(render_feishu_text_style(label, inline_file.get("text_element_style")))
            continue
        if "inline_block" in element:
            inline_block = element["inline_block"] or {}
            label = inline_block.get("block_id", "")
            if label:
                parts.append(label)
    return "".join(parts).replace("\u00a0", " ").strip()


def render_feishu_plain_text_elements(elements: Optional[List[Dict[str, Any]]]) -> str:
    parts: List[str] = []
    for element in elements or []:
        if "text_run" in element:
            text_run = element["text_run"] or {}
            parts.append(text_run.get("content", ""))
            continue
        if "mention_doc" in element:
            mention = element["mention_doc"] or {}
            parts.append(mention.get("token") or "文档")
            continue
        if "mention_user" in element:
            mention = element["mention_user"] or {}
            parts.append(f"@{mention.get('user_id', 'user')}")
            continue
        if "equation" in element:
            equation = element["equation"] or {}
            content = equation.get("content", "")
            if content:
                parts.append(content)
            continue
        if "reminder" in element:
            parts.append("提醒")
            continue
        if "file" in element:
            inline_file = element["file"] or {}
            parts.append(inline_file.get("file_token", "附件"))
            continue
        if "inline_block" in element:
            inline_block = element["inline_block"] or {}
            label = inline_block.get("block_id", "")
            if label:
                parts.append(label)
    return "".join(parts).replace("\u00a0", " ").strip()


def normalize_feishu_code_language(value: Any) -> str:
    if isinstance(value, int):
        return FEISHU_CODE_LANGUAGE_MAP.get(value, "")
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return FEISHU_CODE_LANGUAGE_MAP.get(int(stripped), "")
        return stripped.lower()
    return ""


def choose_markdown_code_fence(text: str) -> str:
    longest = 0
    for match in re.finditer(r"`+", text or ""):
        longest = max(longest, len(match.group(0)))
    return "`" * max(3, longest + 1)


class FeishuMarkdownRenderer:
    def __init__(
        self,
        blocks: List[Dict[str, Any]],
        asset_store: DownloadedAssetStore,
        sheet_loader: Optional[Any] = None,
    ) -> None:
        self.blocks = blocks
        self.asset_store = asset_store
        self.sheet_loader = sheet_loader
        self.heading_counters = [0] * 10
        self.ordered_counters: Dict[Tuple[str, int], int] = {}
        self.sheet_cache: Dict[str, List[List[Any]]] = {}
        self.sheet_errors: Dict[str, str] = {}
        self.block_map = {
            block.get("block_id"): block
            for block in blocks
            if isinstance(block, dict) and block.get("block_id")
        }

    def render(self) -> str:
        root = self.find_root_block()
        lines: List[str] = []
        if root is not None:
            lines.extend(self.render_block(root.get("block_id", "")))
        else:
            root_ids = [
                block.get("block_id", "")
                for block in self.blocks
                if not block.get("parent_id")
            ]
            for block_id in root_ids:
                lines.extend(self.render_block(block_id))
        return finalize_markdown(lines)

    def find_root_block(self) -> Optional[Dict[str, Any]]:
        for block in self.blocks:
            if block.get("block_type") == 1 or "page" in block:
                return block
        for block in self.blocks:
            if not block.get("parent_id"):
                return block
        return None

    def render_children(self, child_ids: List[str], indent: int = 0) -> List[str]:
        lines: List[str] = []
        for child_id in child_ids or []:
            lines.extend(self.render_block(child_id, indent=indent))
        return lines

    def next_ordered_prefix(self, block: Dict[str, Any], indent: int) -> str:
        ordered = block.get("ordered", {}) or {}
        style = ordered.get("style", {}) or {}
        sequence = str(style.get("sequence", "")).strip()
        key = (block.get("parent_id", ""), indent)
        if sequence.isdigit():
            current = int(sequence)
            self.ordered_counters[key] = current
            return f"{current}. "
        current = self.ordered_counters.get(key, 0) + 1
        self.ordered_counters[key] = current
        return f"{current}. "

    def render_block(self, block_id: str, indent: int = 0) -> List[str]:
        block = self.block_map.get(block_id)
        if not block:
            return []
        key = feishu_block_key(block)
        children = block.get("children", []) or []

        if key == "page":
            title = self.render_text_payload(block, "page")
            lines = [f"# {title}", ""] if title else []
            lines.extend(self.render_children(children, indent=indent))
            return lines

        if key.startswith("heading"):
            text = self.render_text_payload(block, key)
            if re.fullmatch(r"\*\*(.+)\*\*", text):
                text = re.sub(r"^\*\*(.+)\*\*$", r"\1", text)
            match = FEISHU_HEADING_KEY_PATTERN.match(key)
            outline_level = int(match.group(1)) if match else 1
            level = min(outline_level + 1, 6)
            if not text:
                return []
            lines = [f"{'#' * level} {text}", ""]
            lines.extend(self.render_children(children, indent=indent))
            return lines

        if key == "text":
            text = self.render_text_payload(block, key)
            if not text:
                return [""]
            prefix = "  " * indent
            return [f"{prefix}{text}", ""]

        if key in ("bullet", "ordered"):
            text = self.render_text_payload(block, key)
            marker = "- " if key == "bullet" else self.next_ordered_prefix(block, indent)
            prefix = "  " * indent
            lines = [f"{prefix}{marker}{text}".rstrip()]
            lines.extend(self.render_children(children, indent=indent + 1))
            return lines

        if key == "todo":
            text = self.render_text_payload(block, key)
            checked = bool(block.get("todo", {}).get("style", {}).get("done"))
            prefix = "  " * indent
            lines = [f"{prefix}- [{'x' if checked else ' '}] {text}".rstrip()]
            lines.extend(self.render_children(children, indent=indent + 1))
            return lines

        if key == "code":
            payload = block.get("code", {}) or {}
            text = render_feishu_plain_text_elements(payload.get("elements"))
            language = normalize_feishu_code_language(payload.get("style", {}).get("language", ""))
            marker = choose_markdown_code_fence(text)
            open_fence = f"{marker}{language}".rstrip()
            return [open_fence, text, marker, ""]

        if key == "quote":
            text = self.render_text_payload(block, key)
            lines = [f"> {line}".rstrip() for line in (text.splitlines() or [""])]
            child_lines = self.render_children(children, indent=indent)
            for line in child_lines:
                lines.append(f"> {line}".rstrip() if line else ">")
            lines.append("")
            return lines

        if key in ("callout", "quote_container"):
            child_lines = self.render_children(children, indent=indent)
            rendered: List[str] = []
            for line in child_lines:
                rendered.append(f"> {line}".rstrip() if line else ">")
            if rendered:
                rendered.append("")
            return rendered

        if key in ("grid", "grid_column", "view"):
            return self.render_children(children, indent=indent)

        if key == "divider":
            return ["---", ""]

        if key == "image":
            image = block.get("image", {}) or {}
            token = image.get("token", "")
            url = self.asset_store.resolve(token, "image.png") if token else ""
            return [f"![image]({url})", ""] if url else []

        if key == "file":
            file_data = block.get("file", {}) or {}
            token = file_data.get("token", "")
            name = file_data.get("name") or "附件"
            url = self.asset_store.resolve(token, name) if token else ""
            return [f"[{name}]({url})", ""] if url else [name, ""]

        if key == "iframe":
            iframe = block.get("iframe", {}) or {}
            component = iframe.get("component", {}) or {}
            url = decode_url(component.get("url", ""))
            return [f"[嵌入内容]({url})", ""] if url else []

        if key == "sheet":
            token = (block.get("sheet", {}) or {}).get("token", "")
            return self.render_sheet(token)

        if key == "add_ons":
            return self.render_addons_block(block)

        if key == "table":
            lines = self.render_table(block)
            if lines:
                lines.append("")
            return lines

        if key == "table_cell":
            return self.render_children(children, indent=indent)

        if children:
            return self.render_children(children, indent=indent)

        text = self.render_text_payload(block, key)
        return [text, ""] if text else []

    def render_table(self, block: Dict[str, Any]) -> List[str]:
        table = block.get("table", {}) or {}
        properties = table.get("property", {}) or {}
        row_size = int(properties.get("row_size") or 0)
        column_size = int(properties.get("column_size") or 0)
        cells = table.get("cells", []) or []

        if row_size <= 0 or column_size <= 0:
            return []

        rows: List[List[str]] = []
        extras: List[List[str]] = []
        index = 0
        for _ in range(row_size):
            row: List[str] = []
            for _ in range(column_size):
                cell_id = cells[index] if index < len(cells) else ""
                cell_text, cell_extras = self.render_table_cell(cell_id)
                row.append(cell_text)
                extras.extend(cell_extras)
                index += 1
            rows.append(row)
        if not rows:
            return []
        header = rows[0]
        separator = ["---"] * column_size
        lines = [
            f"| {' | '.join(header)} |",
            f"| {' | '.join(separator)} |",
        ]
        for row in rows[1:]:
            lines.append(f"| {' | '.join(row)} |")
        if extras:
            lines.append("")
            for extra in extras:
                lines.extend(extra)
                if extra and extra[-1] != "":
                    lines.append("")
        return lines

    def render_table_cell(self, cell_id: str) -> Tuple[str, List[List[str]]]:
        block = self.block_map.get(cell_id)
        if not block:
            return "", []
        children = block.get("children", []) or []
        pieces: List[str] = []
        extras: List[List[str]] = []
        for child_id in children:
            child = self.block_map.get(child_id)
            if child and feishu_block_key(child) == "add_ons":
                rendered = self.render_addons_block(child)
                if rendered:
                    pieces.append("[流程图见下方 Mermaid 代码块]")
                    extras.append(rendered)
                continue
            piece = self.render_inline_block(child_id)
            if piece:
                pieces.append(piece)
        text = "<br>".join(piece for piece in pieces if piece)
        return text.replace("|", r"\|"), extras

    def render_sheet(self, raw_token: str) -> List[str]:
        if not raw_token:
            return []
        if raw_token in self.sheet_cache:
            rows = self.sheet_cache[raw_token]
        elif raw_token in self.sheet_errors:
            rows = []
        elif self.sheet_loader is None:
            rows = []
            self.sheet_errors[raw_token] = "sheet_loader_missing"
        else:
            try:
                rows = self.sheet_loader(raw_token) or []
                self.sheet_cache[raw_token] = rows
            except Exception as exc:
                rows = []
                self.sheet_errors[raw_token] = str(exc)
        lines = markdown_table_from_rows(rows)
        if lines:
            lines.append("")
            return lines
        return [f"[电子表格: {raw_token}]", ""]

    def render_inline_block(self, block_id: str) -> str:
        block = self.block_map.get(block_id)
        if not block:
            return ""
        key = feishu_block_key(block)
        if key in (
            "page",
            "text",
            "heading1",
            "heading2",
            "heading3",
            "heading4",
            "heading5",
            "heading6",
            "heading7",
            "heading8",
            "heading9",
            "bullet",
            "ordered",
            "quote",
            "callout",
        ):
            return self.render_text_payload(block, key)
        if key == "todo":
            text = self.render_text_payload(block, key)
            checked = bool(block.get("todo", {}).get("style", {}).get("done"))
            return f"[{'x' if checked else ' '}] {text}".strip()
        if key == "code":
            return f"`{self.render_text_payload(block, key)}`"
        if key == "image":
            image = block.get("image", {}) or {}
            token = image.get("token", "")
            url = self.asset_store.resolve(token, "image.png") if token else ""
            return f"![image]({url})" if url else ""
        if key == "file":
            file_data = block.get("file", {}) or {}
            token = file_data.get("token", "")
            name = file_data.get("name") or "附件"
            url = self.asset_store.resolve(token, name) if token else ""
            return f"[{name}]({url})" if url else name
        if key == "iframe":
            iframe = block.get("iframe", {}) or {}
            component = iframe.get("component", {}) or {}
            url = decode_url(component.get("url", ""))
            return f"[嵌入内容]({url})" if url else ""
        if key == "divider":
            return "---"
        children = block.get("children", []) or []
        pieces = [self.render_inline_block(child_id) for child_id in children]
        return "<br>".join(piece for piece in pieces if piece)

    def render_addons_block(self, block: Dict[str, Any]) -> List[str]:
        add_ons = block.get("add_ons", {}) or {}
        record_raw = add_ons.get("record", "")
        if isinstance(record_raw, str) and record_raw.strip():
            try:
                record = json.loads(record_raw)
            except json.JSONDecodeError:
                record = {}
            mermaid = record.get("data", "")
            if isinstance(mermaid, str) and mermaid.strip():
                return ["```mermaid", mermaid.strip(), "```", ""]
        return []

    def render_text_payload(self, block: Dict[str, Any], key: str) -> str:
        payload = block.get(key, {}) or {}
        text = render_feishu_text_elements(payload.get("elements"))
        return normalize_space(text) if text else ""


def convert_feishu_source(
    source_url: str,
    output_path: Path,
    assets_dir: Optional[Path],
    upload_client: Optional[UploadClient],
    feishu_client: FeishuClient,
) -> Tuple[int, int]:
    document_token = feishu_client.resolve_doc_token(source_url)
    document_title = feishu_client.get_document_title(document_token)
    blocks = feishu_client.load_all_blocks(document_token)
    asset_store = DownloadedAssetStore(
        feishu_client=feishu_client,
        document_stem=document_title or document_token,
        upload_client=upload_client,
        assets_dir=assets_dir,
    )
    renderer = FeishuMarkdownRenderer(
        blocks,
        asset_store,
        sheet_loader=feishu_client.load_sheet_rows,
    )
    markdown = renderer.render()
    output_path.write_text(markdown, encoding="utf-8")
    for raw_token, error in renderer.sheet_errors.items():
        print(
            f"[feishu->markdown] warning=sheet_fallback token={raw_token} reason={error}",
            file=sys.stderr,
        )
    for token, url in asset_store.cache.items():
        if isinstance(url, str) and url.startswith("http://"):
            print(
                f"[feishu->markdown] warning=mixed_content_risk token={token} url={url}",
                file=sys.stderr,
            )
    return len(asset_store.cache), markdown.count("![image](")


def convert_feishu_html_source(
    source_url: str,
    html_text: str,
    output_path: Path,
    assets_dir: Optional[Path],
    upload_client: Optional[UploadClient],
    session: Any = None,
) -> Tuple[int, int]:
    document_title, blocks, media_sources = parse_feishu_html_source(html_text)
    asset_store = DownloadedAssetStore(
        feishu_client=FeishuHtmlSessionClient(session=session, media_sources=media_sources),
        document_stem=document_title,
        upload_client=upload_client,
        assets_dir=assets_dir,
    )
    renderer = FeishuMarkdownRenderer(blocks, asset_store, sheet_loader=None)
    markdown = renderer.render()
    output_path.write_text(markdown, encoding="utf-8")
    for token, url in asset_store.cache.items():
        if isinstance(url, str) and url.startswith("http://"):
            print(
                f"[feishu->markdown] warning=mixed_content_risk token={token} url={url}",
                file=sys.stderr,
            )
    return len(asset_store.cache), markdown.count("![image](")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a Feishu document link into Markdown for Care-Dev knowledge columns."
    )
    parser.add_argument("input", help="Feishu wiki/docx URL")
    parser.add_argument(
        "--output",
        help="Output Markdown path. Defaults to <document-token>.md in the current directory.",
    )
    parser.add_argument(
        "--assets-dir",
        help="Directory for downloaded images when upload is disabled. Defaults to <doc-stem>-assets next to the output file.",
    )
    parser.add_argument(
        "--html-input",
        help="Path to a saved Feishu page HTML. Parses embedded clientVars instead of using OpenAPI.",
    )
    parser.add_argument(
        "--browser-cookies",
        action="store_true",
        help="Fetch the Feishu page with local browser cookies and parse embedded clientVars.",
    )
    parser.add_argument(
        "--browser",
        default="chrome",
        help="Browser profile to read cookies from when --browser-cookies is enabled. For example: chrome, edge, firefox.",
    )
    parser.add_argument(
        "--cookie-domain",
        default="",
        help="Optional cookie domain override for browser-cookie loading. Defaults to the input URL host suffix.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("CARE_DOCX_BASE_URL", ""),
        help="Care-Dev backend root, admin API root, or full upload endpoint",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("CARE_DOCX_TOKEN", ""),
        help="Care-Dev access token. Raw token or Bearer token both work.",
    )
    parser.add_argument(
        "--directory",
        default=os.getenv("CARE_DOCX_DIRECTORY", ""),
        help="Upload directory such as knowledge/column/12/article/101",
    )
    parser.add_argument(
        "--tenant-id",
        default=os.getenv("CARE_DOCX_TENANT_ID", ""),
        help="Optional tenant-id header value",
    )
    parser.add_argument(
        "--visit-tenant-id",
        default=os.getenv("CARE_DOCX_VISIT_TENANT_ID", ""),
        help="Optional visit-tenant-id header value",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Disable remote uploads and keep extracted images locally",
    )
    parser.add_argument(
        "--feishu-app-id",
        default=os.getenv("CARE_FEISHU_APP_ID", os.getenv("FEISHU_APP_ID", "")),
        help="Feishu app_id for direct link conversion",
    )
    parser.add_argument(
        "--feishu-app-secret",
        default=os.getenv("CARE_FEISHU_APP_SECRET", os.getenv("FEISHU_APP_SECRET", "")),
        help="Feishu app_secret for direct link conversion",
    )
    parser.add_argument(
        "--feishu-base-url",
        default=os.getenv(
            "CARE_FEISHU_BASE_URL",
            os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn"),
        ),
        help="Feishu OpenAPI base URL for direct link conversion",
    )
    return parser


def main() -> int:
    load_env_file(Path(__file__).resolve().parent.parent / ".env.local")
    args = build_parser().parse_args()
    source = args.input.strip()
    if not is_feishu_url(source):
        raise SystemExit("This script only accepts a Feishu wiki/docx URL")

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        fallback_name = "feishu-document.md"
        docx_match = FEISHU_DOCX_URL_PATTERN.search(source) or FEISHU_WIKI_URL_PATTERN.search(source)
        if docx_match:
            fallback_name = f"{docx_match.group(1)}.md"
        output_path = Path.cwd() / fallback_name
    output_path.parent.mkdir(parents=True, exist_ok=True)

    upload_client: Optional[Any] = None
    assets_dir: Optional[Path]
    rustfs_endpoint = os.getenv("CARE_DEV_FILE_S3_ENDPOINT", "").strip()
    rustfs_domain = os.getenv("CARE_DEV_FILE_S3_DOMAIN", "").strip()
    rustfs_bucket = os.getenv("CARE_DEV_FILE_S3_BUCKET", "").strip()
    rustfs_access_key = os.getenv("CARE_DEV_FILE_S3_ACCESS_KEY", "").strip()
    rustfs_secret_key = os.getenv("CARE_DEV_FILE_S3_ACCESS_SECRET", "").strip()
    rustfs_region = os.getenv("CARE_DEV_FILE_S3_REGION", "us-east-1").strip()
    rustfs_path_style = parse_bool(os.getenv("CARE_DEV_FILE_S3_PATH_STYLE", "true"), True)
    effective_directory = (args.directory or os.getenv("CARE_DOCX_DIRECTORY", "")).strip()
    if not effective_directory and rustfs_endpoint:
        effective_directory = "knowledge/column/temp"

    if args.no_upload:
        assets_dir = (
            Path(args.assets_dir).expanduser().resolve()
            if args.assets_dir
            else output_path.with_name(f"{output_path.stem}-assets")
        )
    elif args.base_url or args.token:
        if not args.base_url:
            raise SystemExit("--base-url or CARE_DOCX_BASE_URL is required when upload is enabled")
        if not args.token:
            raise SystemExit("--token or CARE_DOCX_TOKEN is required when upload is enabled")
        if not effective_directory:
            raise SystemExit("--directory or CARE_DOCX_DIRECTORY is required when upload is enabled")
        upload_client = UploadClient(
            base_url=args.base_url,
            token=args.token,
            directory=effective_directory,
            tenant_id=args.tenant_id or None,
            visit_tenant_id=args.visit_tenant_id or None,
        )
        assets_dir = None
    elif all([rustfs_endpoint, rustfs_domain, rustfs_bucket, rustfs_access_key, rustfs_secret_key]):
        upload_client = RustFSUploadClient(
            endpoint=rustfs_endpoint,
            domain=rustfs_domain,
            bucket=rustfs_bucket,
            access_key=rustfs_access_key,
            secret_key=rustfs_secret_key,
            region=rustfs_region,
            directory=effective_directory,
            path_style=rustfs_path_style,
        )
        assets_dir = None
    else:
        assets_dir = (
            Path(args.assets_dir).expanduser().resolve()
            if args.assets_dir
            else output_path.with_name(f"{output_path.stem}-assets")
        )

    if args.html_input:
        html_text = Path(args.html_input).expanduser().read_text(encoding="utf-8", errors="ignore")
        session = (
            load_browser_cookie_session(source, browser=args.browser, cookie_domain=args.cookie_domain)
            if args.browser_cookies
            else None
        )
        uploaded_images, referenced_images = convert_feishu_html_source(
            source_url=source,
            html_text=html_text,
            output_path=output_path,
            assets_dir=assets_dir,
            upload_client=upload_client,
            session=session,
        )
    elif args.browser_cookies:
        session, html_text = fetch_feishu_html_with_browser_cookies(
            source,
            browser=args.browser,
            cookie_domain=args.cookie_domain,
        )
        uploaded_images, referenced_images = convert_feishu_html_source(
            source_url=source,
            html_text=html_text,
            output_path=output_path,
            assets_dir=assets_dir,
            upload_client=upload_client,
            session=session,
        )
    else:
        if not args.feishu_app_id:
            raise SystemExit("--feishu-app-id or CARE_FEISHU_APP_ID is required for Feishu link conversion")
        if not args.feishu_app_secret:
            raise SystemExit("--feishu-app-secret or CARE_FEISHU_APP_SECRET is required for Feishu link conversion")
        feishu_client = FeishuClient(
            app_id=args.feishu_app_id,
            app_secret=args.feishu_app_secret,
            base_url=args.feishu_base_url,
        )
        uploaded_images, referenced_images = convert_feishu_source(
            source_url=source,
            output_path=output_path,
            assets_dir=assets_dir,
            upload_client=upload_client,
            feishu_client=feishu_client,
        )

    if isinstance(upload_client, RustFSUploadClient):
        mode = "rustfs"
    elif upload_client:
        mode = "upload"
    else:
        mode = "local-assets"
    print(f"[feishu->markdown] mode={mode}")
    print(f"[feishu->markdown] output={output_path}")
    print(f"[feishu->markdown] image_refs={referenced_images}")
    print(f"[feishu->markdown] image_targets={uploaded_images}")
    if assets_dir:
        print(f"[feishu->markdown] assets_dir={assets_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
