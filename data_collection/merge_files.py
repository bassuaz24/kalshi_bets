import pandas as pd

df1 = pd.read_csv(".csv")           # add sep=";" if needed
df2 = pd.read_csv(".csv")

out = pd.concat([df1, df2], ignore_index=True)
# optional: drop exact duplicate rows

out.to_csv(".csv", index=False)
