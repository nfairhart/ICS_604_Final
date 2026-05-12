# NBA Prediction Market Bias: Does Pre-Game Media Coverage Skew Market Prices?

**ICS 604: Applied Data Science — Final Project**  
**Author:** Nicholas Fairhart  
**Date:** May 2026
---

## 1. Problem Definition and Motivation

Kalshi has emerged as a legal alternative to traditional sports betting in states like Hawaii, where sports betting remains heavily regulated. Kalshi operates as an open market, much like a stock market, where users can buy and sell contracts based on real-life events. These contracts are priced between zero and one dollar, so the price directly represents the implied probability of that event occurring. For example, a contract priced at $0.75 implies a 75% probability. Unlike conventional sports betting, these markets frame outcomes as binary yes/no propositions, providing a unique opportunity to study how market sentiment aligns with statistical reality. NBA markets are particularly interesting due to significant market size disparities that may introduce exploitable biases. Large-market teams with star players dominate the media landscape, and this domination can lead to systematic biases. When bettors are swayed by coverage rather than underlying statistics, they make predictions inconsistent with realistic outcomes.

For example, the Los Angeles Lakers dominate media coverage as a large-market team featuring historic star LeBron James and newly acquired international sensation Luka Dončić. This media attention may inflate market prices beyond what underlying statistics would support. What initially drew me to exploring sentiment versus reality was the recent NFL playoffs, where the Los Angeles Rams received disproportionate media coverage in the NFC Championship despite losing to the eventual Super Bowl champions, the Seattle Seahawks. This suggested that media sentiment can meaningfully influence market pricing in ways that diverge from actual outcome probabilities.

As market-based prediction platforms replace traditional sports betting, the continuous and reactive nature of these markets makes it increasingly difficult to price outcomes based on ground truth. The goal of this paper is not to condemn these markets, but to detect whether that bias exists and quantify its relationship with pre-game media coverage.

**H₀:** Pre-game media coverage disparity has no effect on Kalshi market-implied probabilities. The losing team's overpricing is unrelated to how much media attention it received relative to the winning team.

**H₁:** Teams receiving disproportionately more pre-game media coverage are systematically overpriced in prediction markets. The probability the market assigns to the eventual loser is higher when the loser had greater media coverage, independent of objective team quality.

---

## 2. Dataset Description

I pulled one year of NBA contract data via the Kalshi API, which included game results and over 1.5 gigabytes of raw trading data. I used a Python script to clean this down to approximately 1,200 rows, one row per game, retaining each team's implied probability as a weighted average of the hour before the game.

Pre-game media coverage was accessed through GDELT (Global Database of Events, Language, and Tone) via Google Cloud BigQuery. GDELT records news article counts and sentiment metrics for entities worldwide. I queried article counts and tone scores for each NBA team in the 48-hour window preceding each game, chosen as a reasonable approximation of the information a bettor would naturally absorb before wagering. Games where either team had fewer than 10 articles were excluded, as sparse coverage may have indicated problems with the data quality.

Rolling pre-game performance statistics including win percentage, point differential, field goal percentage, and rest days were pulled from the NBA API over the same one-year period, reflecting Kalshi's API limit. All three datasets were merged on a shared game identifier. After applying the coverage filter and dropping rows with missing values, the final dataset contained 1,069 games spanning April 2025 to April 2026.

---

## 3. Methodology

The analysis is structured in four parts: a market calibration check, a hypothesis test on media-grouped pricing differences, a logistic regression outcome model, and a comparison between model predictions and Kalshi implied probabilities.

Before testing for media-driven bias, I checked whether Kalshi was broadly well-calibrated. If the market globally over or underpriced all events, any media signal would be confounded. The calibration curve showed Kalshi prices closely follow the ideal calibration line, confirming the market is not globally skewed and that any detected bias is likely attributable to specific factors.

To test whether media attention influences pricing, I split games into two groups based on the direction of media disparity from the losing team's perspective: Group A (loser had more pre-game coverage) and Group B (loser had less). This framing directly captures the scenario where the market may have been misled. Using absolute disparity instead would mix games where the winner got more coverage with games where the loser did, causing the effects to cancel and masking any real signal. I ran a one-sided permutation test with 10,000 iterations to assess whether the observed difference could occur by chance. The permutation test is appropriate because it is non-parametric and does not assume a particular distribution shape, and its results can be interpreted visually through the permutation distribution. A Welch t-test was run alongside as a complementary parametric check.

Because game outcomes are binary, logistic regression is a natural choice for modeling win probability. I selected features with an absolute Pearson correlation greater than 0.05 with the outcome variable to reduce noise and focus on the most predictive signals. Two models were built: a base model using only rolling pregame performance differentials and a media model adding media disparity as an additional feature. The near-identical accuracy of both models suggests media disparity does not meaningfully improve outcome prediction, which becomes important in the final step.

To assess whether Kalshi prices are consistent with the logistic regression, I computed the paired difference between model-predicted probabilities and Kalshi implied probabilities for each game. A one-sample t-test and a bootstrap confidence interval (10,000 iterations) tested whether the mean difference was significantly different from zero. A significant result indicates that Kalshi prices systematically diverge from what the statistical model would predict. The 4.4x dissociation puts the core finding in concrete terms: media disparity explains 7.8% of the variance in Kalshi's pricing errors but only 1.8% of the variance in actual game outcomes, suggesting the market overweights media attention relative to what the underlying competition warrants.

---

## 4. Experiments and Results

The calibration curve confirmed that Kalshi is broadly well-calibrated, with implied probabilities closely following the ideal calibration line and no evidence of global over- or underpricing (Figure 1). The coverage analysis confirmed the expected landscape, with large-market teams like the Lakers dominating pre-game article counts (Figure 2), setting up the central question of whether that attention gap shows up in prices.

The hypothesis test produced a clear result. Losing teams that received more pre-game coverage (Group A, n=486) carried a mean implied probability of 0.4469, compared to 0.3526 for losing teams that received less coverage (Group B, n=583), with an observed difference of 0.0943. The permutation test returned p ≈ 0.0000 and the Welch t-test t=7.56, both rejecting the null with high confidence (Figures 3 and 4). However, this result alone was not fully satisfying. Better teams win more games and attract more media coverage, so the pricing difference could simply reflect team quality rather than media bias. That limitation motivated the logistic regression analysis.

The article versus sentiment comparison revealed that volume, not tone, drives pricing errors. The combined model R² was nearly identical to article-only R², meaning sentiment adds no incremental predictive signal (Figures 5 and 6). NBA media tends toward negativity overall, but what moves market prices is raw headline count rather than the content of those headlines. This reflects how attention works in a social media environment: people absorb volume, not nuance. The 4.4x dissociation makes this concrete: media disparity explains 7.8% of variance in Kalshi's pricing errors (r²=0.0783) but only 1.8% of variance in actual game outcomes (r²=0.0178). The market is reacting to media attention at 4.4 times the rate that media attention actually predicts who wins.

The model versus Kalshi comparison showed that the logistic regression predicted a mean implied probability 1.37% lower than Kalshi across all games (t=-3.01, p=0.0027, 95% CI [-0.0226, -0.0048]), with both the base and media models in agreement (Figure 7). On a per-game basis, 1.37% may seem negligible to an individual bettor. But across thousands of contracts and millions of dollars in trading volume, a systematic 1.37% deviation represents a meaningful and potentially exploitable pricing inefficiency.

---

## 5. Discussion and Insights

The central finding is not simply that media attention correlates with market prices, which alone could be explained by team quality. The meaningful result is the 4.4x dissociation: the market responds to media coverage at more than four times the rate that media coverage actually predicts game outcomes. Kalshi participants appear to be systematically overweighting information that is not proportionally informative about who will win. The volume-not-tone result clarifies the mechanism: it is not how coverage frames a team that moves prices, but how much coverage a team gets. People appear to absorb headline counts rather than content.

A plausible explanation for this bias lies in how sports media operates around prediction markets. Sports analysts and broadcasters routinely state explicit betting positions without providing the underlying statistical context. Prediction market apps like Kalshi lower the barrier to participation, making it easy for casual viewers to act on that commentary immediately. Media attention does not just reflect public interest in a team; it actively generates trading behavior. This feeds the broader concern that these platforms amplify the emotional dimensions of sports fandom in ways that are disconnected from statistical reality.

This analysis has several limitations. Most significantly, the rolling pregame statistics, Kalshi data, and GDELT features were generated with AI assistance and have not been independently audited for accuracy. I treat these datasets with the same trust assumptions applied to any public data source, but I would not consider this work publication-ready without a thorough audit of the underlying pipeline. The analysis covers only one year of data, the maximum available through Kalshi's API. The analysis also establishes correlation, not causation; a formal causal study would be required to confirm the mechanism described above.

For future work, I would prioritize two directions. First, applying formal causal inference methods to establish whether media attention has a direct causal effect on pricing rather than a correlated one. Second, testing whether the detected bias is large and consistent enough to support a profitable trading strategy on Kalshi. The 1.37% systematic deviation identified here is a promising signal, but a rigorous backtesting framework would be needed to determine whether transaction costs and market liquidity make it exploitable in practice.

---

## AI Tool Disclosure

Claude (Anthropic) was used throughout this project in two capacities. First, Claude Code assisted in generating the code for API data collection, including calls to the Kalshi API, GDELT via Google Cloud BigQuery, and the NBA API for rolling pregame statistics. Second, Claude was used as a writing assistant in drafting this report. I described my analysis, methodology, and interpretations in my own words across multiple conversations, and Claude helped organize and polish that language into coherent paragraphs.All analytical design choices reflect my own understanding and judgment. The results and conclusions presented in this report are my own.

---

## Figures

**Figure 1: Kalshi Market Calibration Curve**
![Figure 1: Calibration Curve](https://raw.githubusercontent.com/nfairhart/ICS_604_Final/main/analysis/fig_calibration_curve.png)
**Figure 2: Top 10 Teams by Pre-Game Media Coverage**
![Figure 2: Coverage by Team](https://raw.githubusercontent.com/nfairhart/ICS_604_Final/main/analysis/fig_coverage_by_team.png)
**Figure 3: KDE: Market Overpricing by Media Coverage Direction**
![Figure 3: KDE Distribution](https://raw.githubusercontent.com/nfairhart/ICS_604_Final/main/analysis/fig_sentiment_dist.png)
**Figure 4: Hypothesis Test: Boxplot and Permutation Distribution**
![Figure 4: Hypothesis Test](https://raw.githubusercontent.com/nfairhart/ICS_604_Final/main/analysis/fig_hypothesis_media.png)
**Figure 6: Article vs. Sentiment Explained Variance Comparison**
![Figure 6: Disparity Comparison](https://raw.githubusercontent.com/nfairhart/ICS_604_Final/main/analysis/fig_disparity_comparison.png)
**Figure 7: Bootstrap CI: Model Predictions vs. Kalshi Implied Probability**
![Figure 7: Model vs Kalshi](https://raw.githubusercontent.com/nfairhart/ICS_604_Final/main/analysis/fig_actual_vs_kalshi.png)

---

## References

1. Kalshi. *Kalshi REST API Documentation*. Kalshi, Inc. Retrieved May 2026. https://trading-api.kalshi.com/trade-api/v2/openapi.json
2. NBA Stats. *NBA Stats API*. National Basketball Association. Retrieved May 2026. https://www.nba.com/stats
3. Google Cloud. *GDELT Project: Global Database of Events, Language, and Tone via BigQuery*. The GDELT Project / Google Cloud BigQuery. Retrieved May 2026. https://cloud.google.com/bigquery/public-data


