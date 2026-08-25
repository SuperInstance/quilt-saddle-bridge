"""setup.py — quilt-saddle-bridge."""
from setuptools import setup, find_packages

setup(
    name="quilt-saddle-bridge",
    version="0.1.0",
    description="Bridge between the Quilt's casting-call witness log and saddle's double-entry ledger",
    author="Mavis / Casey DiGennaro",
    license="MIT",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.9",
    install_requires=[
        "quilt-substrate>=0.1.0",
    ],
    extras_require={
        "dev": ["pytest"],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.9",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
)
