from __future__ import print_function
import re
import sys

from .parse import scp_lex
from .parse import scp_yacc
from .compile.renpy import RenpyCompiler
from .compile.word import DocxCompiler
from .compile.analyze import AnalysisCompiler


__version__ = '1.2.1'


class ArgumentError(ValueError):
	def __init__(self, msg):
		super(ValueError, self).__init__(msg)


class FileFormatError(Exception):
	def __init__(self, msg):
		super(Exception, self).__init__(msg)


class OutputWriteError(Exception):
	def __init__(self, msg):
		super(Exception, self).__init__(msg)


class InvalidInputFormatError(ArgumentError):
	def __init__(self, msg):
		super(ArgumentError, self).__init__(msg)


class InvalidOutputFormatError(ArgumentError):
	def __init__(self, msg):
		super(ArgumentError, self).__init__(msg)


class LexerError(Exception):
	def __init__(self, errors, msg):
		super(Exception, self).__init__(msg)
		self.error_messages = errors


class ParserError(Exception):
	def __init__(self, errors, msg):
		super(Exception, self).__init__(msg)
		self.error_messages = errors


def _create_parser():
	# TODO: abstract into parser module
	parser = parse.scp_yacc.parser
	parser.successful = True
	parser.error_messages = []
	return parser


def _create_lexer():
	# TODO: abstract into lexer module
	lexer = parse.scp_lex.lexer
	lexer.successful = True
	lexer.error_messages = []
	return lexer


def _lex_manuscript(script_text, filename):
	symbols = []
	lexer = _create_lexer()
	lexer.input(script_text)
	for tok in lexer:
		symbols.append(tok)
	if not lexer.successful:
		if filename == '-':
			error_file = "(stdin)"
		else:
			error_file = "file '" + filename + "'"
		errors = [error_file + line for line in lexer.error_messages]
		raise LexerError(errors, "encountered problems during lex")
	return symbols


def _parse_manuscript(script_text, filename):
	parser = _create_parser()
	script_ast = parser.parse(script_text)
	if not parser.successful:
		if filename == '--':
			error_file = "(stdin)"
		else:
			error_file = "file '" + filename + "'"
		errors = [error_file + line for line in parser.error_messages]
		raise ParserError(errors, "encountered problems during parse")
	return script_ast


def _parse_symbols(symbols, filename):
	def grab_token():
		for s in symbols:
			yield s
	parser = _create_parser()
	script_ast = parser.parse(tokenfunc=grab_token)
	if not parser.successful:
		if filename == '--':
			error_file = "(stdin)"
		else:
			error_file = "file '" + filename + "'"
		errors = [error_file + line for line in parser.error_messages]
		raise ParserError(errors, "encountered problems during parse")
	return script_ast


def _show_warnings(compiler):
	warns = compiler.get_warnings()
	for w in warns:
		print("Warning: " + w, file=sys.stderr)


def _preprocess(script_ast, target_lang, quiet=False):
	def preproc_includes(ast, lang):
		new_ast = []
		for s in ast:
			if s['type'] == 'line' or s['type'] == 'comment':
				new_ast.append(s)
			elif s['instruction'] == 'IF':
				if_struct = {'type': 'annotation', 'instruction': 'IF', 'branches': []}
				for br in s['branches']:
					if_branch = {'condition': br['condition'], 'statements': preproc_includes(br['statements'], lang)}
					if_struct['branches'].append(if_branch)
				new_ast.append(if_struct)
			elif s['instruction'] == 'WHILE':
				wh_struct = {
					'type': 'annotation',
					'instruction': 'WHILE',
					'condition': s['condition'],
					'statements': preproc_includes(s['statements'], lang)
				}
				new_ast.append(wh_struct)
			elif s['instruction'] == 'INCLUDE':
				if s['langs'] is None or lang in [x[1] for x in s['langs']]:
					if s['parsing'][1]:
						with open(s['file'][1], 'r') as inc_file:
							contents = inc_file.read()
						inc_ast = _parse_manuscript(contents, s['file'][1])
						new_ast += preproc_includes(inc_ast, lang)
					else:
						new_ast.append(s)
			else:
				new_ast.append(s)
		return new_ast
	
	def preproc_chars(ast):
		chars_dict = {}
		for s in ast:
			if s['type'] != 'line' and s['type'] != 'comment':
				if s['instruction'] == 'IF':
					for br in s['branches']:
						preproc_chars(br['statements'])
				elif s['instruction'] == 'WHILE':
					preproc_chars(s['statements'])
				elif s['instruction'] == 'CHARACTERS':
					filename = s['file'][1]
					new_chars = {}
					try:
						new_chars = _read_chars_file(filename)
					except (IOError, FileFormatError) as e:
						if not quiet:
							print("Preprocessor warning: could not process characters file '%s':" % filename, file=sys.stderr)
							print("\t" + e.message, file=sys.stderr)
					chars_dict.update(new_chars)
		return chars_dict

	new_script_ast = preproc_includes(script_ast, target_lang)
	chars = preproc_chars(new_script_ast)
	return new_script_ast, chars


def _read_chars_file(file_path):
	field_names = ('id', 'name', 'color')
	rows = {}
	with open(file_path, 'r') as f:
		ln = 0
		for line in f:
			ln += 1
			fields = []
			line = line.strip()
			r = {}
			ended_with_comma = False
			while len(line) > 0:
				ended_with_comma = False
				if line[0] == ',':
					if len(fields) == 0:
						raise FileFormatError("Line %d: identifier field cannot be empty" % ln)
					fields.append(None)
					ended_with_comma = True
					line = line[1:].strip()
					
				else:
					m = re.match(r"\"[^\"\\]*(?:\\.[^\"\\]*)*\"", line)
					if m is None:
						raise FileFormatError("Line %d: bad format" % ln)
					stripped = ''
					escaping = False
					s = line[m.start():m.end()]
					s = s[1:-1]
					for c in s:
						if c == '\\' and not escaping:
							escaping = True
						else:
							stripped += c
							escaping = False
					fields.append(stripped)
					line = line[m.end():].strip()
					if len(line) > 0 and line[0] == ',':
						ended_with_comma = True
						line = line[1:].strip()
			if ended_with_comma:
				fields.append(None)
			if len(fields) < len(field_names):
				raise FileFormatError("Line %d: bad format" % ln)

			for name, value in zip(field_names, fields):
				r[name] = value
			r['other'] = fields[len(field_names):]
			rows[r['id']] = r
			if r['name'] is None:
				r['name'] = r['id']
	return rows


def _precompile(ast, args, compiler):
	ast, chars = _preprocess(ast, args.output_mode, quiet=args.quiet)
	compiler.set_options(args)
	compiler.set_characters(chars)
	return ast


def _add_renpy_subparser(subparsers, parent):
	rpy_desc = "Compile input(s) to Ren'Py-compatible .rpy format."
	rpy = subparsers.add_parser(
		'renpy', help="Compile to Ren'Py.", description=rpy_desc, parents=[parent]
	)
	""":type : argparse.ArgumentParser"""

	dest_help = 'Set the destination for motion statements that do not explicitly include one.'
	rpy.add_argument('--default-destination', metavar='LOC', default='center', help=dest_help)

	origin_help = 'Set the origin for motion statements that do not explicitly include one.'
	rpy.add_argument('--default-origin', metavar='LOC', default='center', help=origin_help)

	dur_help = 'Set the default time for statements that use a duration but do not explicitly include one.'
	rpy.add_argument('--default-duration', metavar='SECS', default=0.5, type=float, help=dur_help)

	quick_help = "Set the number of seconds that the phrase 'QUICKLY' is interpreted as."
	rpy.add_argument('--quick-speed', metavar='SECS', default=0.25, type=float, help=quick_help)

	slow_help = "Set the number of seconds that the phrase 'SLOWLY' is interpreted as."
	rpy.add_argument('--slow-speed', metavar='SECS', default=2, type=float, help=slow_help)

	tab_help = "Set the number of spaces that are in a single tab in the output."
	rpy.add_argument('--tab-spaces', metavar='SPACES', default=4, type=int, help=tab_help)

	back_help = 'Set the name of the entity that is used for the background in scene statements.'
	rpy.add_argument('--background-entity', metavar='NAME', default='bg', help=back_help)

	cam_help = 'Use the experimental camera system instead of just outputting camera instructions as dialog.'
	rpy.add_argument('--enable-camera', action='store_true', help=cam_help)


def _add_docx_subparser(subparsers, parent):
	docx_desc = "Compile input(s) to a human-readable, script-like .docx format."
	docx = subparsers.add_parser(
		'docx', help="Compile to DOCX.", description=docx_desc, parents=[parent]
	)
	""":type : argparse.ArgumentParser"""

	para_help = 'Set the spacing in pts between each paragraph in the output.'
	docx.add_argument('--paragraph-spacing', metavar='PTS', type=int, default=0, help=para_help)

	flags_help = 'Do not produce any output for FLAG statements in the input file.'
	docx.add_argument('--exclude-flags', dest='include_flags', action='store_false', help=flags_help)

	vars_help = 'Do not produce any output for VAR statements in the input file.'
	docx.add_argument('--exclude-vars', dest='include_vars', action='store_false', help=vars_help)

	python_help = 'Produce minimal output for PYTHON statements in the input file.'
	docx.add_argument('--exclude-python', dest='include_python', action='store_false', help=python_help)

	title_help = 'Set the title for the script. This will be at the top of all output files.'
	docx.add_argument('--title', default=None, help=title_help)


def _add_lex_subparser(subparsers, parent):
	lex_desc = "Perform lexical tokenization on the input(s) without parsing or compiling, and output the symbol list."
	lex = subparsers.add_parser(
		'lex', help="Lex the contents without parsing.", description=lex_desc, parents=[parent]
	)
	""":type : argparse.ArgumentParser"""

	lex.add_argument('--pretty', action='store_true', help= "Output pretty-print formatted list of symbols.")


def _add_ast_subparser(subparsers, parent):
	ast_desc = "Parse the input(s) into an abstract syntax tree without compiling, and output the AST."
	ast = subparsers.add_parser(
		'ast', help="Parse the contents without compiling.", description=ast_desc, parents=[parent]
	)
	""":type : argparse.ArgumentParser"""

	ast.add_argument('--pretty', action='store_true', help= "Output pretty-print formatted AST.")


def _add_analyze_subparser(subparsers, parent):
	ana_desc = "Perform an analysis on the identifiers and references that the final output will require"
	ana_desc += " implementations for."
	ana = subparsers.add_parser(
		'analyze', help="Perform static analysis.", description=ana_desc, parents=[parent]
	)
	""":type : argparse.ArgumentParser"""

def _parse_args():
	# TODO: argparse not available before python 2.7; if we want compat before then we need a rewrite
	import argparse

	parser = argparse.ArgumentParser(description="Compiles manuscripts to other formats")

	parser.add_argument('--version', action='version', version="%(prog)s " + __version__)

	# these args will not be properly parsed if we just add them to the root parser
	parent_parser = argparse.ArgumentParser(add_help=False)
	input_help = "The file(s) to be compiled. Will be compiled in order. If no input files are specified, scrappy will"
	input_help += " read from stdin."
	parent_parser.add_argument('input', nargs='*', type=argparse.FileType('r'), default=[sys.stdin], help=input_help)

	# space at the end of metavar is not a typo; we need it so help output is prettier
	subparsers = parser.add_subparsers(
		description="Functionality to execute.", metavar="SUBCOMMAND", dest='output_mode'
	)
	subparsers.required = True

	_add_renpy_subparser(subparsers, parent_parser)
	_add_docx_subparser(subparsers, parent_parser)
	_add_lex_subparser(subparsers, parent_parser)
	_add_ast_subparser(subparsers, parent_parser)
	_add_analyze_subparser(subparsers, parent_parser)

	quiet_help = "Suppress compiler warnings. This will not suppress errors reported by the lexer and parser."
	parser.add_argument('--quiet', '-q', action='store_true', help=quiet_help)

	output_help = "The file to write the compiled manuscript to. If no output file is specified, scrappy will write to"
	output_help += "stdout."
	parser.add_argument('--output', '-o', type=argparse.FileType('w'), default=sys.stdout, help=output_help)

	fmt_help = "The format of the input(s)."
	parser.add_argument('--format', '-f', default='scp', choices=('scp', 'lex', 'ast'), help=fmt_help)

	try:
		args = parser.parse_args()
	except argparse.ArgumentError as e:
		raise ArgumentError(e.message)

	return args


def parse_cli_and_execute():
	import pprint

	args = _parse_args()

	if args.output == sys.stdout and args.output_mode == 'docx':
		raise InvalidOutputFormatError("cannot output DOCX file to stdout")

	# first, load in all source files and convert to a single AST or symbol list (if only lexing):
	input_data = []
	for input_file in args.input:
		file_contents = input_file.read()
		input_file.close()

		# TODO: what is filename for stdout/stdin on argparse.FileType?

		if args.output_mode == 'lex':
			if args.format == 'scp':
				lex_symbols = _lex_manuscript(file_contents, input_file.name)
			elif args.format == 'lex':
				lex_symbols = eval(file_contents)
			else:
				raise InvalidInputFormatError("to output lexer symbols, input format must be scp or lex")
			input_data += lex_symbols
		else:
			if args.format == 'scp':
				ast = _parse_manuscript(file_contents, input_file.name)
			elif args.format == 'lex':
				ast = _parse_symbols(eval(file_contents), input_file.name)
			elif args.format == 'ast':
				ast = eval(file_contents)
			else:
				raise InvalidInputFormatError(
					"to output AST or compiled formats, input format must be scp, lex, or ast")
			input_data += ast

	# now compile as necessary
	if args.output_mode == 'ast' or args.output_mode == 'lex':
		# already done, we got the desired format during input processing
		output_data = input_data
	else:
		# preprocess and compile
		if args.output_mode == 'renpy':
			compiler = RenpyCompiler()
		elif args.output_mode == 'docx':
			compiler = DocxCompiler()
		elif args.output_mode == 'analyze':
			compiler = AnalysisCompiler()
		else:
			raise ValueError("Unknown output mode '" + args.output_mode + "'")

		ast = _precompile(input_data, args, compiler)
		output_data = compiler.compile_script(ast)
		if not args.quiet:
			_show_warnings(compiler)

	# finally, write the output to disk
	if (args.output_mode == 'lex' or args.output_mode == 'ast') and args.pretty:
		pprint.pprint(output_data, args.output)
	elif args.output_mode == 'docx':
		# docx is saved via framework's save() method
		args.output.close()
		try:
			output_data.save(args.output.name)
		except IOError as e:
			if e.errno == 13:
				raise OutputWriteError("permission denied")
			else:
				raise
	else:
		args.output.write(str(output_data))

	# close the file
	args.output.close()


def run():
	try:
		parse_cli_and_execute()
	except ArgumentError as e:
		print("Fatal error: " + e.message, file=sys.stderr)
		sys.exit(1)
	except OutputWriteError as e:
		print("Fatal write error: " + e.message, file=sys.stderr)
		print("Make sure that the output file is not open in another application")
		sys.exit(2)
	except LexerError as e:
		for msg in e.error_messages:
			print(msg, file=sys.stderr)
		print("Lexing failed", file=sys.stderr)
		sys.exit(3)
	except ParserError as e:
		for msg in e.error_messages:
			print(msg, file=sys.stderr)
		print("Parsing failed", file=sys.stderr)
		sys.exit(4)


if __name__ == "__main__":
	run()
