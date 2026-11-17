from setuptools import setup, find_packages

setup(
    name="pubchem2sqlite",
    version="0.4.0",
    license="MIT",
    packages=find_packages(exclude=["*.tests", "*.tests.*", "tests"]),
    package_data={"pubchem_local": ["compound_layout.json", "static/*", "static/ghs/*"]},
    python_requires=">=3.8",

    # The SQLite builder itself only needs the standard library.
    install_requires=[],
    extras_require={
        # Web UI / REST API server ('pubchem-local serve')
        "server": [
            "fastapi>=0.110,<1",
            "uvicorn>=0.29",
            "rdkit>=2023.9",
            # CDK structure depictions (needs Java and the jars from java/pom.xml; optional at runtime)
            "JPype1>=1.5",
        ],
    },
    entry_points={
        "console_scripts": ["pubchem-local=pubchem_local.cli:main"],
    },

    # Metadata
    author="Eric Bach",
    author_email="eric.bach@aalto.fi",
    description="Build and serve a local copy of the PubChem compound database using SQLite.",
    url="https://github.com/mcaragon/local_pubchem_db",
)
