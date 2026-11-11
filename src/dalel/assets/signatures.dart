import 'dart:convert';
import 'dart:io';
import 'package:analyzer/dart/analysis/utilities.dart';
import 'package:analyzer/dart/ast/ast.dart';

// Parse syntax, not generated-code text patterns. Restrict to the requested
// abstract localization class so delegates/constructors/static helpers vanish.
void main(List<String> args) {
  if (args.length != 2) throw ArgumentError('file.dart ClassName');
  final unit = parseString(content: File(args[0]).readAsStringSync(), throwIfDiagnostics: true).unit;
  final classes = unit.declarations.whereType<ClassDeclaration>().where((c) => c.name.lexeme == args[1]).toList();
  if (classes.length != 1) throw StateError('Expected exactly one requested class');
  final rows = <Map<String, Object?>>[];
  for (final member in classes.single.members.whereType<MethodDeclaration>()) {
    if (member.isStatic || !member.isAbstract || member.isSetter) continue;
    final parameters = <Map<String, Object?>>[];
    for (final outer in member.parameters?.parameters ?? <FormalParameter>[]) {
      final parameter = outer is DefaultFormalParameter ? outer.parameter : outer;
      if (parameter is! SimpleFormalParameter) throw StateError('Unexpected message parameter form');
      parameters.add({
        'name': parameter.name?.lexeme,
        'type': parameter.type?.toSource() ?? 'dynamic',
        'kind': outer.isNamed ? 'named' : 'positional',
        'required': outer.isRequiredNamed || outer.isRequiredPositional,
      });
    }
    rows.add({
      'name': member.name.lexeme,
      'kind': member.isGetter ? 'getter' : 'method',
      'return_type': member.returnType?.toSource() ?? 'dynamic',
      'parameters': parameters,
    });
  }
  rows.sort((a, b) => (a['name'] as String).compareTo(b['name'] as String));
  stdout.writeln(jsonEncode(rows));
}
