# Double Dipper

The weekly recap for the Double Dipper fantasy league: **https://double-dip.alexeldeib.xyz**

Every Tuesday at 7pm Pacific, a GitHub Action pulls the week from Sleeper, picks the trophies, has Claude write the jokes, and redeploys the site. Share the link once; it updates itself.

## One-time setup

Give the Action an Anthropic API key so Claude can write the jokes. Without one, the site still updates with plain trophy labels.

```bash
gh secret set ANTHROPIC_API_KEY -R alexeldeib/double-dipper
```

## Knobs

- `prompt.md` sets the writer's voice and league lore. Add in-jokes, rivalries, and past champions there.
- `template.html` controls the look. `recap.py` builds the facts and the page.
- To redo a week, open **Actions → Weekly recap → Run workflow**. Enter a week number, and tick **fresh** to rewrite the jokes.

## Local

```bash
python3 recap.py          # recap the latest scored week
python3 recap.py render   # rebuild docs/ from weeks/*.json
```
