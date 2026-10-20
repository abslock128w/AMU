"""Which changed functions side-by-side execution and the generated tier take as targets (sidebyside.targets)."""
from amu.core import sidebyside


def test_test_file_functions_and_docstring_only_changes_are_not_targets_and_say_why():
    # Found in the retrospective (defect 8): Django #21820 SimplifiedURLTests.test_invalid_view and #21861
    # ModelTest.test_eq were targets, and so were sympy #30697 / #30734 functions whose only change is the docstring.
    view0 = ('class SimplifiedURLTests:\n    def test_invalid_view(self):\n        msg = "view must be a callable."\n'
             '        self.check(msg)\n')
    view1 = view0.replace('a callable.', 'a callable, but got str.')
    poly0 = ('def cyclotomic_poly(n, x=None, polys=False):\n    """Generates cyclotomic polynomial of order `n` in `x`.\n\n'
             '    polys : bool, optional\n        ``polys=True`` returns an expression, otherwise\n    """\n'
             '    if n <= 0:\n        raise ValueError(n)\n    return n\n\n\n'
             'def random_poly(x, n):\n    """Generates a polynomial of degree ``n``."""\n    return x ** n\n\n\n'
             'def swinnerton_dyer_poly(n):\n    if n <= 0:\n        raise ValueError(n)\n    return n\n\n\n'
             'def nth(n):\n    """The nth term."""\n    return n\n\n\n'
             '@staticmethod\ndef wrapped(n):\n    return n\n')
    poly1 = (poly0.replace('``polys=True`` returns an expression', 'Returns a Poly object when ``polys=True``')
             .replace('"""Generates a polynomial of degree ``n``."""\n    return x ** n',
                      '"""Generates a polynomial of degree ``n``, as an expression."""\n    return x ** (n + 1)')
             .replace('def swinnerton_dyer_poly(n):\n    if n <= 0:',
                      'def swinnerton_dyer_poly(n):\n    """Generates n-th Swinnerton-Dyer polynomial."""\n    if n <= 0:  # no order 0')
             .replace('"""The nth term."""\n    return n', '"""The nth term."""\n    return (\n        n\n    )')
             .replace('@staticmethod\ndef wrapped', '@classmethod\ndef wrapped'))
    case0 = 'def skip_if(flag):\n    return flag\n'
    case1 = 'def skip_if(flag):\n    return not flag\n'
    before = {'tests/urlpatterns/tests.py': view0, 'sympy/polys/specialpolys.py': poly0, 'django/test/testcases.py': case0}
    after = {'tests/urlpatterns/tests.py': view1, 'sympy/polys/specialpolys.py': poly1, 'django/test/testcases.py': case1}
    functions = [dict(file='tests/urlpatterns/tests.py', symbol='SimplifiedURLTests.test_invalid_view'),
                 *(dict(file='sympy/polys/specialpolys.py', symbol=name)
                   for name in ('cyclotomic_poly', 'random_poly', 'swinnerton_dyer_poly', 'nth', 'wrapped')),
                 dict(file='django/test/testcases.py', symbol='skip_if')]
    found, skipped = sidebyside.targets(functions, before, after)
    # A docstring changed together with the code is still a target, and django/test/ is production code, not tests.
    assert [t['id'] for t in found] == ['sympy/polys/specialpolys.py#random_poly', 'django/test/testcases.py#skip_if'], found
    unchanged = 'only its docstring, comments or layout changed'
    assert skipped == {'tests/urlpatterns/tests.py#SimplifiedURLTests.test_invalid_view': 'in a test file',
                       'sympy/polys/specialpolys.py#cyclotomic_poly': unchanged,
                       'sympy/polys/specialpolys.py#swinnerton_dyer_poly': unchanged,
                       'sympy/polys/specialpolys.py#nth': unchanged}, skipped
    # A change to decorators alone is still no target and no entry: decorators are never compared.
    assert 'sympy/polys/specialpolys.py#wrapped' not in skipped
