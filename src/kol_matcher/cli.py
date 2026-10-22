import typer

app = typer.Typer()

@app.command()
def analyze():
    typer.echo("Not implemented")
    
@app.command()
def match():
    typer.echo("Not implemented")

@app.command()
def backtest():
    typer.echo("Not implemented")