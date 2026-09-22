# Risk Policy

## Default rules

1. Determine structural invalidation before position size.
2. Default portfolio risk ceiling: **2% of equity**.
3. Correlated trades are grouped into one risk bucket (`crypto_beta` initially).
4. Leverage does not define trade risk; position size × stop distance does.
5. Do not tighten the structural stop merely to justify an oversized position.
6. Scale-ins must fit inside the original maximum risk budget.
7. A profitable rule-breaking trade is still graded as poor execution.

## Example

Account equity: 4,898 USDT

- BNB short 5.54 @ 786.40, stop 812 → 141.82 USDT risk
- ETH short 0.80 @ 2,740.20, stop 2,820 → 63.84 USDT risk
- Combined correlated risk → 205.66 USDT ≈ 4.2%

Under a 2% default risk budget, the system should flag the portfolio as over budget and block planned adds in the decision-support UI.
