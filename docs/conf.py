import moa

project = "my-own-accelerator"
author = "my-own-accelerator contributors"
release = moa.__version__

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.coverage",
    "sphinx.ext.duration",
    "sphinx.ext.githubpages",
    "sphinx.ext.graphviz",
    "sphinx.ext.ifconfig",
    "sphinx.ext.intersphinx",
    "sphinx.ext.linkcode",
    "sphinx.ext.mathjax",
    "sphinx.ext.napoleon",
    "sphinx.ext.todo",
    "sphinx_copybutton",
    "sphinx_gallery.gen_gallery",
    "sphinx_issues",
    "sphinxcontrib.mermaid",
    "matplotlib.sphinxext.plot_directive",
    "sphinx_runpython.epkg",
    "sphinx_runpython.gdot",
    "sphinx_runpython.runmermaid",
    "sphinx_runpython.runpython",
]

exclude_patterns = ["_build"]
html_theme = "pydata_sphinx_theme"
html_static_path = ["_static"]


def linkcode_resolve(domain: str, info: dict[str, str]) -> str | None:
    return None
