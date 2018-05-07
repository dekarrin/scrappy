import re

from .parse import scp_lex
from .parse import scp_yacc
from .compile.renpy import RenpyCompiler
from .compile.word import DocxCompiler


__version__ = '1.1.0'


_parser = None
_lexer = None
_comp_renpy = None
_comp_word = None


def get_parser():
	global _parser
	if _parser is None:
		_parser = parse.scp_yacc.parser
	return _parser


def get_lexer():
	global _lexer
	if _lexer is None:
		_lexer = parse.scp_lex.lexer
	return _lexer


def renpy_compiler():
	global _comp_renpy
	if _comp_renpy is None:
		_comp_renpy = RenpyCompiler()
	return _comp_renpy


def word_compiler():
	global _comp_word
	if _comp_word is None:
		_comp_word = DocxCompiler()
	return _comp_word


def lex_manuscript(script_text):
	symbols = []
	lexer = parse.scp_lex.lexer
	lexer.successful = True
	lexer.input(script_text)
	for tok in lexer:
		symbols.append(tok)
	return symbols


def parse_manuscript(script_text):
	parser = parse.scp_yacc.parser
	parser.successful = True
	script_ast = parser.parse(script_text)
	return script_ast


def parse_symbols(symbols):
	def grab_token():
		for s in symbols:
			yield s
	parser = parse.scp_yacc.parser
	parser.successful = True
	script_ast = parser.parse(tokenfunc=grab_token)
	return script_ast


def compile_to_renpy(manuscript_ast):
	compiler = renpy_compiler()
	return compiler.compile_script(manuscript_ast)


def compile_to_word(manuscript_ast):
	compiler = word_compiler()
	return compiler.compile_script(manuscript_ast)


def show_warnings(compiler):
	warns = compiler.get_warnings()
	for w in warns:
		print("Compiler warning: " + w)


def preprocess(script_ast, target_lang, quiet=False):
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
						inc_ast = parse_manuscript(contents)
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
						new_chars = read_chars_file(filename)
					except (IOError, FileFormatError) as e:
						if not quiet:
							print("Preprocessor warning: could not process characters file '%s':" % filename)
							print("\t" + str(e))
					chars_dict.update(new_chars)
		return chars_dict

	new_script_ast = preproc_includes(script_ast, target_lang)
	chars = preproc_chars(new_script_ast)
	return new_script_ast, chars


class FileFormatError(Exception):
	def __init__(self, msg):
		super(Exception, self).__init__(msg)


def read_chars_file(file_path):
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


def run():
	def precompile(fn_ast, fn_args, compiler):
		fn_ast, chars = preprocess(fn_ast, fn_args.output_mode, quiet=fn_args.quiet)
		compiler.set_options(fn_args)
		compiler.set_characters(chars)
		return fn_ast

	import argparse
	import pprint
	import sys

	class InvalidInputFormatException(Exception):
		pass

	class InvalidOutputFormatException(Exception):
		pass

	try:
		argparser = argparse.ArgumentParser(description="Compiles manuscripts to other formats")
		argparser.add_argument('--version', action='version', version="%(prog)s " + __version__)
		argparser.add_argument('--input', '-i', action='append', help="The file(s) to be compiled. Will be compiled in order. If no input files are specified, scrappy will read from stdin.")
		argparser.add_argument('--output', '-o', nargs=1, help="The file to write the compiled manuscript to. If no output file is specified, scrappy will write to stdout.")
		argparser.add_argument('--pretty', action='store_true', help="Output pretty-print format. Only applies when output is a raw python type.")
		argparser.add_argument('--inputformat', '-f', nargs=1, dest='input_mode', default=['scp'], choices=('scp', 'lex', 'ast'), help="The format of the input(s).")
		argparser.add_argument('--quiet', '-q', action='store_true', help="Suppress compiler warnings. This will not suppress errors reported by the lexer and parser.")
		modegroup = argparser.add_mutually_exclusive_group()
		modegroup.add_argument('--renpy', '-r', dest='output_mode', action='store_const', const='renpy', help="Compile input(s) to Ren'Py-compatible .rpy format. This is the default mode.")
		modegroup.add_argument('--word', '-w', dest='output_mode', action='store_const', const='word', help="Compile input(s) to .docx format.")
		modegroup.add_argument('--lex', '-l', dest='output_mode', action='store_const', const='lex', help="Perform lexical analysis on the input(s) without parsing or compiling.")
		modegroup.add_argument('--ast', dest='output_mode', action='store_const', const='ast', help="Parse the input(s) into an abstract syntax tree without compiling.")
		wordopts = argparser.add_argument_group('human-readable (DOCX) compiler options')
		wordopts.add_argument('--h-paragraph-spacing', metavar='PTS_SPACING', dest='paragraph_spacing', type=int, default=0, help='Set the spacing in pts between each paragraph in the output.')
		wordopts.add_argument('--h-exclude-flags', dest='include_flags', action='store_false', help='Do not produce any output for FLAG statements in the input file.')
		wordopts.add_argument('--h-exclude-vars', dest='include_vars', action='store_false', help='Do not produce any output for VAR statements in the input file.')
		wordopts.add_argument('--h-exclude-python', dest='include_python', action='store_false', help='Produce minimal output for PYTHON statements in the input file.')
		wordopts.add_argument('--h-title', dest='title', default=None, help='Set the title for the script. This will be at the top of all output files.')
		renpyopts = argparser.add_argument_group("ren'py compiler options")
		renpyopts.add_argument('--r-default-destination', metavar='LOCATION', default='center', dest='default_destination', help='Set the destination for motion statements that do not explicitly include one.')
		renpyopts.add_argument('--r-default-origin', metavar='LOCATION', default='center', dest='default_origin', help='Set the origin for motion statements that do not explicitly include one.')
		renpyopts.add_argument('--r-default-duration', metavar='SECONDS', default=0.5, type=float, dest='default_duration', help='Set the default time for statements that use a duration but do not explicitly include one.')
		renpyopts.add_argument('--r-quick-speed', metavar='SECONDS', default=0.25, dest='quick_speed', type=float, help="Set the number of seconds that the phrase 'QUICKLY' is interpreted as.")
		renpyopts.add_argument('--r-slow-speed', metavar='SECONDS', default=2, dest='slow_speed', type=float, help="Set the number of seconds that the phrase 'SLOWLY' is interpreted as.")
		renpyopts.add_argument('--r-tab-spaces', metavar='SPACES', default=4, dest='tab_spaces', type=int, help='Set the number of spaces that are in a single tab in the output.')
		renpyopts.add_argument('--r-background-entity-name', metavar='NAME', default='bg', dest='background_ent', help='Set the name of the entity that is used for the background in scene statements.')
		renpyopts.add_argument('--r-enable-camera', action='store_true', dest='enable_camera', help='Use the experimental camera system instead of just outputting camera instructions as dialog.')

		argparser.set_defaults(output_mode='renpy', output=['--'])
		args = argparser.parse_args()
		args.input_mode = args.input_mode[0]
		args.output = args.output[0]

		if args.input is None:
			args.input = ['--']  # don't pass into set_defaults() or else '--' will always be present

		output_file = None
		if args.output == '--':
			if args.output_mode == 'word':
				raise InvalidOutputFormatException("cannot output DOCX file to stdout")
			output_file = sys.stdout

		for filename in args.input:
			output = None
			success = True
			if filename == '--':
				file_contents = sys.stdin.read()
			else:
				with open(filename, 'r') as r_file:
					file_contents = r_file.read()

			if args.output_mode == 'lex':
				if args.input_mode == 'scp':
					output = lex_manuscript(file_contents)
					success = get_lexer().successful
				elif args.input_mode == 'lex':
					output = eval(file_contents)
				else:
					raise InvalidInputFormatException("to output lexer symbols, input format must be scp or lex")
			else:
				if args.input_mode == 'scp':
					ast = parse_manuscript(file_contents)
					success = get_parser().successful
				elif args.input_mode == 'lex':
					ast = parse_symbols(eval(file_contents))
					success = get_parser().successful
				elif args.input_mode == 'ast':
					ast = eval(file_contents)
				else:
					raise InvalidInputFormatException(
						"to output AST or compiled formats, input format must be scp, lex, or ast")
				if success:
					if args.output_mode == 'ast':
						output = ast
					elif args.output_mode == 'renpy':
						ast = precompile(ast, args, renpy_compiler())
						output = compile_to_renpy(ast)
						if not args.quiet:
							show_warnings(renpy_compiler())
					elif args.output_mode == 'word':
						ast = precompile(ast, args, word_compiler())
						output = compile_to_word(ast)
						if not args.quiet:
							show_warnings(word_compiler())

			if success:
				if output_file is None and args.output_mode != 'word':
					output_file = open(args.output, 'w')

				if (args.output_mode == 'lex' or args.output_mode == 'ast') and args.pretty:
					pprint.pprint(output, output_file)
				elif args.output_mode == 'word':
					try:
						output.save(args.output)
					except IOError as e:
						if e.errno == 13:
							print("Error writing file: permission denied")
							print("Make sure that '" + args.output + "' is not open in another application")
				else:
					output_file.write(str(output))
		if args.output != '--' and output_file is not None:
			output_file.close()
	except (InvalidInputFormatException, InvalidOutputFormatException) as e:
		print("Fatal error: " + e.message)


if __name__ == "__main__":
	run()
