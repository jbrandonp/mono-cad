from __future__ import annotations

import ast


ALLOWED_MODULES: frozenset[str] = frozenset({"build123d", "bd", "math", "numpy", "np"})
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "open",
        "compile",
        "__import__",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "breakpoint",
        "input",
        "system",
        "popen",
        "spawn",
    }
)


class SecurityViolationError(PermissionError):
    """Exception levée lorsqu'un script viole les règles de sécurité du bac à sable AST."""


class SecurityASTValidator(ast.NodeVisitor):
    """Analyseur statique AST vérifiant l'innocuité d'un script CAO ECIP.

    Cette classe parcourt l'arbre syntaxique abstrait (AST) avant toute exécution
    pour garantir l'absence d'accès aux fichiers, au réseau, aux processus système
    ou à l'introspection privée de Python.
    """

    def visit_Import(self, node: ast.Import) -> None:
        """Contrôle les instructions d'importation directes (`import module`)."""
        for alias in node.names:
            base_module = alias.name.split(".")[0]
            if base_module not in ALLOWED_MODULES:
                raise SecurityViolationError(
                    f"Importation non autorisée du module externe : '{alias.name}'. "
                    f"Seuls les modules {sorted(ALLOWED_MODULES)} sont permis."
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Contrôle les instructions d'importation ciblées (`from module import ...`)."""
        if not node.module:
            raise SecurityViolationError("Les importations relatives sont interdites dans le bac à sable.")

        base_module = node.module.split(".")[0]
        if base_module not in ALLOWED_MODULES:
            raise SecurityViolationError(
                f"Importation non autorisée depuis le module : '{node.module}'. "
                f"Seuls les modules {sorted(ALLOWED_MODULES)} sont permis."
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Interdit les appels aux fonctions internes dangeureuses (eval, exec, open)."""
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_NAMES:
            raise SecurityViolationError(
                f"Appel de fonction système interdit détecté : '{node.func.id}'."
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Empêche le débridage de bac à sable via les attributs magiques (__class__, __subclasses__)."""
        if node.attr.startswith("__"):
            raise SecurityViolationError(
                f"Accès formellement interdit à l'attribut privé ou magique : '{node.attr}'."
            )
        self.generic_visit(node)


def validate_cad_script(script_code: str) -> None:
    """Valide statiquement un code source Python avant exécution dans l'interpréteur.

    Args:
        script_code: Chaîne de caractères contenant le script Python généré.

    Raises:
        SecurityViolationError: Si une instruction dangereuse est identifiée.
        SyntaxError: Si le code n'est pas un code Python syntaxiquement valide.
    """
    tree = ast.parse(script_code)
    validator = SecurityASTValidator()
    validator.visit(tree)
