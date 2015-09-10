#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Extension for flake8 to test for unindexed format parameters."""
from __future__ import print_function, unicode_literals

import re
import sys

try:
    import argparse
except ImportError as e:
    argparse = e

if sys.version_info[0] > 2:
    unicode = str

from ast import NodeVisitor, PyCF_ONLY_AST, Bytes, Str

__version__ = '0.1.0dev2'


class Flake8Argparse(object):

    def __init__(self, tree, filename):
        self.tree = tree
        self.filename = filename

    @classmethod
    def add_options(cls, parser):
        class Wrapper(object):
            def add_argument(self, *args, **kwargs):
                # flake8 uses config_options to handle stuff like 'store_true'
                if kwargs.get('action') == 'store_true':
                    for opt in args:
                        if opt.startswith('--'):
                            break
                    else:
                        opt = args[0]
                    parser.config_options.append(opt.lstrip('-'))
                parser.add_option(*args, **kwargs)

        cls.add_arguments(Wrapper())

    @classmethod
    def add_arguments(cls, parser):
        pass


def create_parser(plugin_class, codes):
    def handler(value):
        if value:
            ignored = set(value.split(','))
            unrecognized = ignored - codes
            ignored &= codes
            if unrecognized:
                invalid = set()
                for invalid_code in unrecognized:
                    no_valid = True
                    if not invalid:
                        for valid_code in codes:
                            if valid_code.startswith(invalid_code):
                                ignored.add(valid_code)
                                no_valid = False
                    if no_valid:
                        invalid.add(invalid_code)
                if invalid:
                    raise argparse.ArgumentTypeError(
                        'The code(s) is/are invalid: "{0}"'.format(
                            '", "'.join(invalid)))
            return ignored
        else:
            return set()

    if isinstance(argparse, ImportError):
        print('argparse is required for the standalone version.')
        return False

    parser = argparse.ArgumentParser()
    parser.add_argument('--ignore', type=handler, default='',
                        help='Ignore the given comma-separated codes')
    parser.add_argument('files', nargs='+')
    plugin_class.add_arguments(parser)
    return parser


def handle_plugin(plugin_class, parser, args):
    args = parser.parse_args(args)
    if hasattr(plugin_class, 'parse_options'):
        plugin_class.parse_options(args)
    failed = False
    for filename in args.files:
        with open(filename, 'rb') as f:
            tree = compile(f.read(), filename, 'exec', PyCF_ONLY_AST, True)
        for line, char, msg, checker in plugin_class(tree, filename).run():
            if msg[:4] not in args.ignore:
                print('{0}:{1}:{2}: {3}'.format(filename, line, char + 1, msg))
                failed = True
    return not failed


def execute(plugin_class, args, choices):
    parser = create_parser(plugin_class, choices)
    if parser is not False:
        return handle_plugin(plugin_class, parser, args)
    else:
        return False


class TextVisitor(NodeVisitor):

    """
    Node visitor for bytes and str instances.

    It tries to detect docstrings as string of the first expression of each
    module, class or function.
    """

    OUTSIDE = 0
    IN_MODULE = 1
    IN_CLASS = 2
    IN_FUNCTION = 3
    IN_ARGUMENTS = 4

    def __init__(self):
        super(TextVisitor, self).__init__()
        self.nodes = []
        self._docstring = self.OUTSIDE

    def visit_Str(self, node):
        self.nodes += [node]

    def visit_Bytes(self, node):
        self.nodes += [node]

    def visit_Expr(self, node):
        if (self._docstring in (self.IN_MODULE, self.IN_CLASS, self.IN_FUNCTION) and
                isinstance(node.value, (Str, Bytes))):
            node.value.is_docstring = True
        super(TextVisitor, self).generic_visit(node)
        self._docstring = self.OUTSIDE

    def visit_Module(self, node):
        self._docstring = self.IN_MODULE
        super(TextVisitor, self).generic_visit(node)
        self._docstring = self.OUTSIDE

    def visit_ClassDef(self, node):
        self._docstring = self.IN_CLASS
        super(TextVisitor, self).generic_visit(node)
        self._docstring = self.OUTSIDE

    def visit_FunctionDef(self, node):
        self._docstring = self.IN_FUNCTION
        super(TextVisitor, self).generic_visit(node)
        self._docstring = self.OUTSIDE

    def visit_arguments(self, node):
        in_func = self._docstring == self.IN_FUNCTION
        if in_func:
            self._docstring = self.IN_ARGUMENTS
        super(TextVisitor, self).generic_visit(node)
        if in_func:
            assert self._docstring == self.IN_ARGUMENTS
            self._docstring = self.IN_FUNCTION

    def generic_visit(self, node):
        if self._docstring != self.IN_ARGUMENTS:
            self._docstring = 0
        super(TextVisitor, self).generic_visit(node)


class UnindexedParameterChecker(Flake8Argparse):

    UNICODE_REGEX = re.compile(r'\{[:\}]')
    BYTES_REGEX = re.compile(br'\{[:\}]')

    version = __version__
    name = 'flake8-unindexed-parameter'

    def _generate_error(self, node):
        if getattr(node, 'is_docstring', False):
            msg = 'P102 docstring does contain unindexed parameters'
        else:
            msg = 'P101 str does contain unindexed parameters'
        return node.lineno, node.col_offset, msg, type(self)

    def run(self):
        visitor = TextVisitor()
        visitor.visit(self.tree)
        for node in visitor.nodes:
            if ((isinstance(node.s, bytes) and
                    self.BYTES_REGEX.search(node.s)) or
                    (isinstance(node.s, unicode) and
                     self.UNICODE_REGEX.search(node.s))):
                yield self._generate_error(node)


def main(args):
    choices = set(['P101'])
    return execute(UnindexedParameterChecker, args, choices)


if __name__ == '__main__':
    main(sys.argv[1:])
