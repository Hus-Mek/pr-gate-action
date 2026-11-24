# Claude Code in pr-gate-action

## Done means it works for the user

Green CI doesn't make a feature, a fix, or any code change done. Before you call it done, use it
the way its user will meet it, and read what they would see:

- Open a real test PR in a consumer repo whose caller workflow points at your branch. Watch the
  `attribution`, `classify`, `review` and `fix` jobs run, then read the `risk:T*` label and the
  `<!-- pr-gate -->` summary comment as a PR author would.
- Try the case you changed: a PR that should fail, one that should pass, and a PR over the size cap.
- Put what you ran and what the user saw in the pull request body. If you couldn't try a change
  this way, say so and why.
