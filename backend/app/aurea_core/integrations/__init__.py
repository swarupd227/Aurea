"""Pluggable integration adapters for external APIs.

Each sub-package (identity, custodian, transfer) exposes a factory function
``get_adapter()`` that resolves the active implementation from the environment
variable ``AUREA_<SERVICE>_PROVIDER``.  Defaults to the mock implementation
so the platform runs without any external credentials.

To swap in a real integration:
  1. Implement the abstract base class in a new module (e.g. ``identity/socure.py``).
  2. Register it in the sub-package's ``_REGISTRY`` dict.
  3. Set the env var, e.g. ``AUREA_IDENTITY_PROVIDER=socure``.
"""
