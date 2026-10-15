"""Explanations (A20): why EEPY made each nudge decision, in a form a spoken line can use.

    catalog.py   the evidence types: which features each covers, its placeholders, its
                 default sentences (python -m eepy.explanations.catalog writes
                 docs/EXPLANATION_CATALOG.md)
    build.py     the Explanation object for one decision, from the HMM's evidence ledger
                 (hmm mode) or the rules that fired (rules mode)
    check.py     the faithfulness diagnostic over a recorded session (sum and deletion test)

Plain words, method and limits: docs/A20_EXPLANATIONS.md. Python 3.9, standard library only
(the vision process imports the catalog).
"""
