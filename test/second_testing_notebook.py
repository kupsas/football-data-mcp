import marimo

__generated_with = "0.18.4"
app = marimo.App(width="medium")


@app.cell
def _():
    import sys
    from rootutils import find_root

    # Same as other notebooks/tests: anchor to repo root, not a relative folder name.
    sys.path.append(str(find_root() / "src"))
    import ScraperFC as sfc
    return (sfc,)


@app.cell
def _(sfc):
    ss = sfc.Sofascore()

    ss.get_valid_seasons("Saudi Arabia Pro League")
    return


if __name__ == "__main__":
    app.run()
