from pathlib import Path

from setuptools import setup, find_namespace_packages

HERE = Path(__file__).resolve().parent

setup(
    name="cli-anything",
    version=(HERE / "VERSION").read_text().strip(),
    description="CLI interface for the GoHighLevel API",
    author="Lead Gen Jay",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    package_data={
        "cli_anything.gohighlevel": ["skills/*.md"],
    },
    install_requires=[
        "click>=8.0.0",
        "prompt-toolkit>=3.0.0",
        "requests>=2.28.0",
        "rich>=13.0.0",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-gohighlevel=cli_anything.gohighlevel.gohighlevel_cli:main",
            "ghl=cli_anything.gohighlevel.gohighlevel_cli:main",
        ],
    },
    python_requires=">=3.10",
)
