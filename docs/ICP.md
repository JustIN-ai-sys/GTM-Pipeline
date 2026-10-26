# Ideal customer profile

The score is only as good as this definition. Change the weights in `icp_rules` and the signals
in `signal_rules` to match your own.

## Accounts

- **Industry**: software and fintech first (weights 20 and 18); healthcare 14; logistics 10;
  energy 8; retail 5; education 3. Anything not listed gets 0 and, with
  `--require-icp-industry`, is not enriched at all.
- **Size**: 50-500 employees scores 10, 11-49 or 501-2,000 scores 6, anything else 2. Small
  enough to reach a decision maker, big enough to have a process worth improving.
- **Signals**: a confirmed CRM signal (HubSpot 4, Salesforce 4, Pipedrive 3, cap 10) means the
  team already runs a sales process in a CRM.

## People to look up

First choice titles: head of RevOps / sales operations, VP sales, chief of staff, CFO,
founder (at the smaller accounts). One or two contacts per account is enough to start.

## Who is excluded

Customers, opt-outs, bounced addresses and accounts someone else is working go in the
`suppressions` table (CSV: `Kind,Value,Reason`, kind = `email`, `domain` or `org`).
