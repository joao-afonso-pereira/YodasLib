import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="YodasLib",
    version="v0.0.0",
    author="YODAS (Your Omnipotent Data Scientists) - WDL 2022",
    author_email="joaoafonsoppereira@gmail.com",
    description="Utils and local app for dark corridors optimization.",
    long_description=long_description,
    url="https://github.com/joao-afonso-pereira/YodasLib",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
    ],
    install_requires=[
        'numpy',
        'geopandas'
        'pandas',
        'matplotlib',
        'Dijkstar',
        'scipy'
    ],
)
