from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlsplit, urlunsplit

ListingType = Literal["sale", "newhouse"]

_SALE_PATTERN = re.compile(r"^/home/house/detail/[1-9][0-9]*/[1-9][0-9]*\.html$")
_NEWHOUSE_PATTERN = re.compile(r"^/[1-9][0-9]*(?:/detail)?/?$")
_SHORT_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{2,64}$")

_ACCEPTED_DIRECT_HOSTS: set[str] = {
    "sale.591.com.tw",
    "newhouse.591.com.tw",
}

_ACCEPTED_INITIAL_HOSTS: set[str] = {
    "sale.591.com.tw",
    "newhouse.591.com.tw",
    "591.to",
}

_ACCEPTED_REDIRECT_HOSTS: set[str] = {
    "sale.591.com.tw",
    "newhouse.591.com.tw",
    "591.to",
}


@dataclass(frozen=True)
class Initial591Url:
    request_url: str
    kind: Literal["direct", "short"]


@dataclass(frozen=True)
class Validated591DetailUrl:
    canonical_url: str
    listing_type: ListingType
    source_listing_id: str


class Unsupported591Url(ValueError):
    pass


def _check_unsafe_components(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise Unsupported591Url("only HTTPS is supported")
    if parts.username or parts.password:
        raise Unsupported591Url("embedded credentials are not allowed")
    if parts.port not in (None, 443):
        raise Unsupported591Url("non-default ports are not allowed")
    if _is_ip_literal(parts.hostname):
        raise Unsupported591Url("IP literals are not allowed")
    for _, value in parse_qsl(parts.query, keep_blank_values=True):
        nested = urlsplit(value)
        if nested.scheme or nested.netloc:
            raise Unsupported591Url("nested URLs are not allowed")


def _is_ip_literal(hostname: str | None) -> bool:
    if hostname is None:
        return False
    import ipaddress

    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def _canonicalize(url: str) -> str:
    parts = urlsplit(url)
    hostname = (parts.hostname or "").lower()
    return urlunsplit(("https", hostname, parts.path, "", ""))


def _request_url(url: str) -> str:
    parts = urlsplit(url)
    hostname = (parts.hostname or "").lower()
    return urlunsplit(("https", hostname, parts.path, parts.query, ""))


def _is_sale_path(path: str) -> bool:
    return bool(_SALE_PATTERN.fullmatch(path))


def _is_newhouse_path(path: str) -> bool:
    return bool(_NEWHOUSE_PATTERN.fullmatch(path))


def _path_listing_id(path: str, listing_type: ListingType) -> str:
    if listing_type == "sale":
        segments = path.rstrip("/").split("/")
        return segments[-1].removesuffix(".html")
    segments = path.strip("/").split("/")
    return segments[0]


def parse_initial_591_url(raw_url: str) -> Initial591Url:
    _check_unsafe_components(raw_url)
    parts = urlsplit(raw_url)
    hostname = parts.hostname
    if hostname is None:
        raise Unsupported591Url("no hostname")
    if hostname not in _ACCEPTED_INITIAL_HOSTS:
        raise Unsupported591Url(f"unsupported initial host: {hostname}")
    path = parts.path
    if hostname == "591.to":
        token = path.lstrip("/")
        if not _SHORT_TOKEN_PATTERN.fullmatch(token):
            raise Unsupported591Url("invalid short token")
        return Initial591Url(request_url=_request_url(raw_url), kind="short")
    if hostname in _ACCEPTED_DIRECT_HOSTS:
        if hostname == "sale.591.com.tw" and not _is_sale_path(path):
            raise Unsupported591Url("invalid sale path")
        if hostname == "newhouse.591.com.tw" and not _is_newhouse_path(path):
            raise Unsupported591Url("invalid newhouse path")
        return Initial591Url(request_url=_request_url(raw_url), kind="direct")
    raise Unsupported591Url("unsupported host")


def validate_redirect_target(raw_url: str) -> None:
    _check_unsafe_components(raw_url)
    parts = urlsplit(raw_url)
    hostname = parts.hostname
    if hostname is None or hostname not in _ACCEPTED_REDIRECT_HOSTS:
        raise Unsupported591Url(f"unsupported redirect target: {hostname}")
    path = parts.path
    if hostname == "591.to":
        token = path.lstrip("/")
        if not _SHORT_TOKEN_PATTERN.fullmatch(token):
            raise Unsupported591Url("invalid short token")
        return
    if hostname == "sale.591.com.tw":
        if not _is_sale_path(path):
            raise Unsupported591Url("invalid sale path")
        return
    if hostname == "newhouse.591.com.tw":
        if not _is_newhouse_path(path):
            raise Unsupported591Url("invalid newhouse path")
        return
    raise Unsupported591Url("unsupported host")


def validate_final_591_url(raw_url: str) -> Validated591DetailUrl:
    _check_unsafe_components(raw_url)
    parts = urlsplit(raw_url)
    hostname = parts.hostname
    if hostname is None:
        raise Unsupported591Url("no hostname")
    if hostname == "591.to":
        raise Unsupported591Url("short URL is not a valid final URL")
    if hostname not in _ACCEPTED_DIRECT_HOSTS:
        raise Unsupported591Url(f"unsupported final host: {hostname}")
    path = parts.path
    if hostname == "sale.591.com.tw":
        if not _is_sale_path(path):
            raise Unsupported591Url("invalid sale path")
        listing_type: ListingType = "sale"
    elif hostname == "newhouse.591.com.tw":
        if not _is_newhouse_path(path):
            raise Unsupported591Url("invalid newhouse path")
        listing_type = "newhouse"
    else:
        raise Unsupported591Url("unsupported host")
    canonical = _canonicalize(raw_url)
    listing_id = _path_listing_id(path, listing_type)
    return Validated591DetailUrl(
        canonical_url=canonical,
        listing_type=listing_type,
        source_listing_id=listing_id,
    )
