import scryacc
from compile.renpy import RenpyCompiler

def convert_to_renpy(input_string):
	parser = scryacc.parser
	script = parser.parse(input_string)
	compiler = RenpyCompiler()
	return compiler.compile_script(script)