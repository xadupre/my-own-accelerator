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
html_logo = "_static/logo.svg"

sphinx_gallery_conf = {
    "examples_dirs": ["../examples"],
    "gallery_dirs": ["auto_examples"],
    "filename_pattern": r"/plot_",
    # Heavy examples (model downloads, conversions) are documented but not
    # executed during the documentation build.
    "ignore_pattern": r"plot_qwen3_int4_load_save\.py",
}

epkg_dictionary = {
    "onnx": "https://onnx.ai/",
    "onnx_ir": "https://pypi.org/project/onnx-ir/",
    "onnxruntime-genai": "https://github.com/microsoft/onnxruntime-genai",
}


def linkcode_resolve(domain: str, info: dict[str, str]) -> str | None:
    return None
