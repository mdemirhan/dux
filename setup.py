from setuptools import Extension, setup

setup(
    ext_modules=[
        Extension("dux._walker", sources=["csrc/walker.c"]),
        Extension("dux._matcher", sources=["csrc/matcher.c"]),
    ]
)
