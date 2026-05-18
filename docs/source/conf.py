# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
import sys
from rootutils import find_root
sys.path.insert(0, str(find_root() / "src"))


# -- Project information ---------------------------------------------------------------------------
project = "ScraperFC"
copyright = "2022, Owen Seymour"
author = "Owen Seymour"


# -- General configuration -------------------------------------------------------------------------
# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named "sphinx.ext.*") or your custom
# ones.
extensions = ["sphinx.ext.autodoc", "sphinx.ext.napoleon", "sphinx.ext.intersphinx", "myst_nb"]

# Botasaurus (via ``botasaurus_requests``) downloads a native helper from GitHub on first import.
# CI runners share IPs and quickly hit ``api.github.com`` unauthenticated rate limits, which makes
# autodoc fail to import ``ScraperFC`` and turns those failures into warnings (-W then fails the
# build). Stub these packages so API docs build offline.
autodoc_mock_imports = [
    "botasaurus",
    "botasaurus_requests",
    "botasaurus_driver",
    "botasaurus_api",
    "botasaurus_proxy_authentication",
    "botasaurus_humancursor",
    "bota",
    "close_chrome",
]

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = ["*.ipynb_checkpoints"]

# Resolve :class:`pandas.DataFrame`, typing from stdlib, etc. (cached locally after first fetch).
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pandas": ("https://pandas.pydata.org/pandas-docs/stable/", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}

# Ignore warnings for some types not being found
nitpick_ignore = [
    ("py:class", "pd.DataFrame"),
    ("py:class", "pandas.core.frame.DataFrame"),
    ("py:class", "optional"),
    ("py:class", "default True"),
    ("py:class", "default False"),
    ("py:class", "dicts"),
    ("py:class", "bs4.BeautifulSoup"),
    ("py:class", "BeautifulSoup"),
    ("py:class", "bs4.element.Tag"),
    ("py:class", "bs4.element.NavigableString"),
    ("py:class", "botasaurus_requests.request_class.Request"),
    ("py:class", "botasaurus_requests.response.Response"),
    ("py:class", "botasaurus.request.Request"),
    ("py:class", "botasaurus.browser.Driver"),
    ("py:class", "botasaurus_driver.driver.Driver"),
    ("py:class", "FBrefMatch"),
    ("py:class", "ScraperFC.fbref_match.FBrefMatch"),
    ("py:class", "SofascorePlayer"),
    ("py:class", "ScraperFC.sofascore_player.SofascorePlayer"),
]

# HTML theme
html_theme = "furo"

# myst-nb config options
nb_execution_mode = "off"
