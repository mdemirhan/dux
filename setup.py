from setuptools import Extension, setup

setup(ext_modules=[Extension("dux._walker", sources=["dux/_walker.c"])])
