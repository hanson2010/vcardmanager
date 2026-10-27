"""Merge, split, clean, strip and deduplicate vCard (.vcf) files."""

from vcardmanager.model import Property, VCard
from vcardmanager.parser import iter_vcards, parse_text, read_vcards
from vcardmanager.writer import dumps, write_vcards

__version__ = "0.1.0"

__all__ = ["Property", "VCard", "__version__", "dumps", "iter_vcards", "parse_text", "read_vcards", "write_vcards"]
