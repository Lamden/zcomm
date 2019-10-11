from setuptools import setup

setup(
    name='zcomm',
    version='0.0.1',
    packages=['zcomm'],
    url='https://github.com/Lamden/zcomm',
    license='GPL3',
    author='Stuart Farmer',
    author_email='stuart@lamden.io',
    description='PyZMQ structures and objects to help write microservices.',
    install_requires=['zmq']
)
