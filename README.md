# DuckDB Reporting — Three-Layer Modeling Stack

A minimal, working example of three linked modeling layers for DuckDB, all defined in YAML. The layers separate *what things mean* from *how they're queried* from *how they're stored*.

```
ontology/           <- conceptual layer: entities, properties, relationships, rules
semantic_models/    <- business layer: dimensions, measures, metrics (dbt/MetricFlow style)
data_model/         <- physical layer: DuckDB table schemas (columns, types, partitioning)
generated_ddl/      <- output of generate_ddl.py (DuckDB DDL, regenerated on demand)
scripts/
  generate_ddl.py       <- data_model/*.yaml -> generated_ddl/*.sql
  validate_mapping.py   <- checks ontology -> semantic -> data_model references are consistent
  duckdb_mcp_server.py  <- MCP server exposing DuckDB over stdio for opencode
data.duckdb         <- local DuckDB database (auto-seeded from generated_ddl/)
opencode.json       <- opencode config with DuckDB MCP server
```

---

## The Three Layers

The dependency flows in one direction:

```
Ontology  --maps to-->  Semantic Model  --built on-->  Data Model  --lives in-->  DuckDB
```

| Layer | Question it answers | Changes how often | Owned by |
|---|---|---|---|
| **Ontology** | What *is* a Customer, an Account — and how do they relate? | Rarely | Domain experts, data architects |
| **Semantic model** | What metrics and dimensions can I query? | Occasionally | Analytics engineers |
| **Data model** | What tables, columns, and types physically exist? | Frequently | Data engineers |

---

## 1. Ontology Layer

The conceptual description of business entities, their properties, relationships, and business rules — independent of any database or query language. This is the most stable layer; systems change but the underlying business concepts stay true.

Each entity YAML contains:

- **Entity** and description
- **Properties** — typed attributes (e.g. `hasBalance: Money`)
- **Relationships** — how entities connect, with cardinality (e.g. one Customer holds many Accounts)
- **Rules** — constraints, integrity checks, and derivations (e.g. `balance_after = balance_before + amount`)
- **`maps_to`** — pointers to the semantic model (entity, dimensions, measures)

### Entities

| Entity | Table | Description |
|---|---|---|
| Customer | `core.customer` | A party who has signed up and may hold Accounts and Mortgages |
| SavingAccount | `core.saving_account` | A deposit account that earns interest |
| CurrentAccount | `core.current_account` | A transactional account for everyday spending |
| MortgageAccount | `core.mortgage_account` | A secured loan used to finance a real estate purchase |
| Transaction | `core.transaction` | A financial event that debits or credits an Account balance |

### Example: Customer Ontology

```yaml
entity: Customer
description: "A party who has signed up and may hold Accounts and Mortgages"
properties:
  - name: hasRegion
    range: Region
    description: "The sales region the customer belongs to"
  - name: hasLifetimeValue
    range: Money
    description: "Cumulative revenue attributed to this customer"
  - name: hasStatus
    range: ActiveStatus
    description: "Whether the customer is currently active"
rules:
  - name: hasAtLeastOneAccount
    description: "A Customer must hold at least one Account"
    type: integrity
    expression: "Customer[customer_id] implies exists SavingAccount[customer_id] or exists CurrentAccount[customer_id] or exists MortgageAccount[customer_id]"
  - name: requiresActiveStatusForPositiveLtv
    description: "A Customer with a positive lifetime value must be active"
    type: constraint
    expression: "lifetime_value > 0 implies is_active = true"
maps_to:
  semantic_model: customer
  entity: customer
  dimensions: [region, is_active]
  measures: [lifetime_value, customer_count]
```

### Rules by Entity

| Entity | Rule | Type | Expression |
|---|---|---|---|
| Customer | `hasAtLeastOneAccount` | integrity | Must hold at least one account |
| Customer | `requiresActiveStatusForPositiveLtv` | constraint | Positive LTV implies active |
| SavingAccount | `nonNegativeBalance` | constraint | Balance >= 0 |
| SavingAccount | `validInterestRate` | constraint | Rate between 0 and 1 |
| SavingAccount | `interestAccrual` | derivation | `daily_interest = balance * rate / 365` |
| CurrentAccount | `overdraftBound` | constraint | Balance >= -overdraft_limit |
| CurrentAccount | `debitCardRequiresActive` | constraint | Debit card implies active status |
| CurrentAccount | `overdraftRequiresInterestRate` | constraint | Overdraft limit requires interest rate |
| MortgageAccount | `outstandingWithinLoan` | constraint | Outstanding <= loan_amount |
| MortgageAccount | `positiveInterestRate` | constraint | Rate > 0 |
| MortgageAccount | `maturityAfterOrigination` | constraint | Maturity > origination date |
| MortgageAccount | `amortizationSchedule` | derivation | Standard amortization formula |
| Transaction | `nonZeroAmount` | constraint | Amount != 0 |
| Transaction | `accountingIdentity` | derivation | `balance_after = balance_before + amount` |
| Transaction | `signByType` | constraint | Deposits positive, withdrawals negative |
| Transaction | `postsToExactlyOne` | integrity | Posts to exactly one account type |

---

## 2. Semantic Model Layer

The business-facing abstraction over physical tables — the metrics, dimensions, and relationships that analysts and BI tools actually query. Uses dbt MetricFlow YAML syntax.

Each semantic model YAML contains:

- **Model** — `ref('table')` linking to the data model
- **Entities** — primary and foreign keys (for joins)
- **Dimensions** — categorical or time attributes for filtering/grouping
- **Measures** — raw aggregations (sum, count, avg, etc.)

And a top-level **Metrics** section for named, reusable business calculations.

### Semantic Models and Metrics

**customer**

| Metric | Description | Based on |
|---|---|---|
| `total_ltv` | Total lifetime value across all customers | `sum(lifetime_value)` |
| `active_customers` | Count of active customers | `count_distinct(customer_id)` where `is_active = true` |

**current_account**

| Metric | Description | Based on |
|---|---|---|
| `total_current_balance` | Total balance across all current accounts | `sum(balance)` |
| `total_current_accounts` | Total number of current accounts | `count_distinct(current_account_id)` |
| `avg_current_account_balance` | Average balance per current account | `avg(balance)` |

**saving_account**

| Metric | Description | Based on |
|---|---|---|
| `total_saving_balance` | Total balance across all savings accounts | `sum(balance)` |
| `total_saving_accounts` | Total number of savings accounts | `count_distinct(saving_account_id)` |
| `avg_saving_account_balance` | Average balance per savings account | `avg(balance)` |

**mortgage_account**

| Metric | Description | Based on |
|---|---|---|
| `total_mortgage_balance` | Total outstanding mortgage balance | `sum(outstanding_balance)` |
| `total_mortgages` | Total number of mortgage accounts | `count_distinct(mortgage_account_id)` |
| `total_originated_volume` | Total original loan amount | `sum(loan_amount)` |
| `avg_mortgage_rate` | Average interest rate across mortgages | `avg(interest_rate)` |

**transaction**

| Metric | Description | Based on |
|---|---|---|
| `total_volume` | Total transaction volume | `sum(amount)` |
| `total_transactions` | Total number of transactions | `count(transaction_id)` |
| `net_cash_flow` | Net cash flow (credits minus debits) | `sum(amount)` |
| `withdrawal_volume` | Total amount withdrawn | `sum(amount)` where `transaction_type = 'withdrawal'` |

### Example: Customer Semantic Model

```yaml
semantic_models:
  - name: customer
    model: ref('customer')
    entities:
      - name: customer
        type: primary
        expr: customer_id
    dimensions:
      - name: region
        type: categorical
      - name: signup_date
        type: time
        type_params:
          time_granularity: day
      - name: is_active
        type: categorical
    measures:
      - name: lifetime_value
        agg: sum
      - name: customer_count
        agg: count_distinct
        expr: customer_id

metrics:
  - name: total_ltv
    type: simple
    type_params:
      measure: lifetime_value
  - name: active_customers
    type: simple
    type_params:
      measure: customer_count
    filter: |
      {{ Dimension('customer__is_active') }} = true
```

---

## 3. Data Model Layer

The physical structure of your data in DuckDB — table names, column names, types, partitioning, and clustering. This YAML is compiled into `CREATE TABLE` DDL.

Each data model YAML contains:

- **Table/dataset/project** — fully qualified DuckDB table reference
- **Partitioning** — date column for partition pruning (cost and performance)
- **Clustering** — columns for optimized filtering/joining
- **Columns** — name, DuckDB type (`STRING`, `NUMERIC`, `DATE`, `BOOL`, `INT64`), mode (`REQUIRED`/`NULLABLE`), description

### Tables

| Table | Columns | Partitioned by | Clustered by |
|---|---|---|---|
| `core.customer` | customer_id, signup_date, region, lifetime_value, is_active | signup_date | region, customer_id |
| `core.current_account` | current_account_id, customer_id, balance, currency, overdraft_limit, overdraft_interest_rate, has_debit_card, opened_date, status | opened_date | customer_id, overdraft_limit |
| `core.saving_account` | saving_account_id, customer_id, balance, currency, interest_rate, minimum_balance, opened_date, status | opened_date | customer_id, interest_rate |
| `core.mortgage_account` | mortgage_account_id, customer_id, property_address, property_value, loan_amount, outstanding_balance, currency, interest_rate, interest_rate_type, loan_term_months, monthly_payment, origination_date, maturity_date, status | origination_date | customer_id, status |
| `core.transaction` | transaction_id, account_id, transaction_type, amount, currency, balance_before, balance_after, description, transaction_date, status | transaction_date | account_id, transaction_type |

### Example: Customer Data Model

```yaml
table: customer
dataset: core
project: your_gcp_project
partition_by:
  field: signup_date
  type: DATE
cluster_by: [region, customer_id]
columns:
  - name: customer_id
    type: STRING
    mode: REQUIRED
    description: "Primary key"
  - name: signup_date
    type: DATE
    mode: REQUIRED
  - name: region
    type: STRING
    mode: NULLABLE
  - name: lifetime_value
    type: NUMERIC
    mode: NULLABLE
  - name: is_active
    type: BOOL
    mode: NULLABLE
```

### How Layers Connect (Customer example)

1. **Ontology** says: *"A Customer has a lifetime value."* (stable business fact)
2. **Semantic model** says: *"`lifetime_value` is a `sum` measure; `total_ltv` is the metric analysts query."* (governed definition)
3. **Data model** says: *"`lifetime_value` is a `NUMERIC` column in `core.customer`, clustered by `region`."* (physical reality)

If a column is renamed or a table migrated, only the data model and its direct semantic mapping change — the ontology and metric names stay untouched.

---

## Files by Layer

| Entity | Ontology | Semantic Model | Data Model | DDL |
|---|---|---|---|---|
| Customer | `ontology/customer.yaml` | `semantic_models/customer.yml` | `data_model/customer.yaml` | `generated_ddl/customer.sql` |
| SavingAccount | `ontology/saving_account.yaml` | `semantic_models/saving_account.yml` | `data_model/saving_account.yaml` | `generated_ddl/saving_account.sql` |
| CurrentAccount | `ontology/current_account.yaml` | `semantic_models/current_account.yml` | `data_model/current_account.yaml` | `generated_ddl/current_account.sql` |
| MortgageAccount | `ontology/mortgage_account.yaml` | `semantic_models/mortgage_account.yml` | `data_model/mortgage_account.yaml` | `generated_ddl/mortgage_account.sql` |
| Transaction | `ontology/transaction.yaml` | `semantic_models/transaction.yml` | `data_model/transaction.yaml` | `generated_ddl/transaction.sql` |

---

## Querying with DuckDB via opencode

### Setup

```bash
pip install duckdb pyyaml --break-system-packages
```

### Generate DDL

```bash
python scripts/generate_ddl.py
```

This reads `data_model/*.yaml` and produces `generated_ddl/*.sql` (DuckDB DDL).

### Validate Layer Consistency

```bash
python scripts/validate_mapping.py
```

Checks that all `maps_to` references in the ontology resolve to existing semantic model dimensions/measures, and that semantic model expressions resolve to existing data model columns. Run this in CI on every PR that touches any model layer.

### Start the MCP Server (opencode)

The DuckDB MCP server is configured in `opencode.json`:

```json
{
  "mcp": {
    "duckdb": {
      "type": "local",
      "command": ["python3", "scripts/duckdb_mcp_server.py"],
      "enabled": true,
      "env": {
        "DUCKDB_PATH": "data.duckdb"
      }
    }
  }
}
```

When you launch opencode in this project, the MCP server starts automatically. It:

1. Opens (or creates) the DuckDB database at `data.duckdb`
2. On first connect, auto-seeds tables from `generated_ddl/*.sql` — converting DuckDB types to DuckDB equivalents (STRING→VARCHAR, NUMERIC→DECIMAL, BOOL→BOOLEAN, etc.)
3. Exposes a `run_query` tool over stdio (JSON-RPC 2.0, MCP protocol `2024-11-05`)

### Query via opencode

Once the MCP server is running, ask opencode to run SQL against the database. Examples:

```
Show me all customers in the NA region
```

```
What is the total outstanding mortgage balance by status?
```

```
Run a query: SELECT customer_id, count(*) as account_count
FROM core.current_account GROUP BY customer_id
```

Under the hood, opencode calls the `run_query` tool:

```json
{
  "sql": "SELECT * FROM core.customer WHERE region = 'NA'",
  "max_results": 100
}
```

The tool returns JSON with `columns`, `rows`, `row_count`, and `truncated`.

### Query Directly (without opencode)

```python
import duckdb
conn = duckdb.connect('data.duckdb', read_only=True)
result = conn.execute("SELECT * FROM core.customer LIMIT 5").fetchall()
conn.close()
```

### Regenerate DDL (for BigQuery deployment)

```bash
python scripts/generate_ddl.py --apply
```

Requires the `bq` CLI to be configured with appropriate credentials.

---

## How the LLM Uses the Three Layers

When you ask opencode a question like *"What's the total mortgage balance by region?"*, the LLM doesn't just guess at SQL — it reasons through the three layers to construct a correct, meaningful query.

### Step 1: Ontology — Understand the Business Concept

The LLM reads the ontology files to understand what entities exist and how they relate:

- "mortgage balance" maps to the `MortgageAccount` entity
- `MortgageAccount` has an `outstanding_balance` property and is held by a `Customer` (many-to-one)
- `Customer` has a `hasRegion` property

This tells the LLM that mortgages and customers are separate entities joined by `customer_id`, not the same table.

### Step 2: Semantic Model — Find the Right Metric

The LLM reads the semantic model to find the governed metric definitions:

- `mortgage_account` semantic model has an `outstanding_balance` measure (`sum` of `outstanding_balance`)
- `customer` semantic model has a `region` dimension and a `customer` foreign key entity

This tells the LLM which aggregation to use (`sum`), which column to aggregate, and that it needs to join `mortgage_account` to `customer` to get `region`.

### Step 3: Data Model — Write Correct SQL

The LLM reads the data model to get the exact physical schema:

- `core.mortgage_account` has columns `customer_id` (STRING) and `outstanding_balance` (NUMERIC)
- `core.customer` has columns `customer_id` (STRING) and `region` (STRING)

This gives the LLM the exact table names, column names, and join keys to write:

```sql
SELECT c.region, sum(m.outstanding_balance) AS total_outstanding
FROM core.mortgage_account m
JOIN core.customer c ON m.customer_id = c.customer_id
GROUP BY c.region
```

### Step 4: Execute via MCP

The LLM calls the `run_query` tool with the SQL, gets back the results, and presents them to you in natural language.

### Why This Matters

| Without the layers | With the layers |
|---|---|
| LLM guesses at table/column names — often wrong | LLM reads exact schema from data model YAML |
| LLM invents its own metric definitions | LLM uses governed metrics from semantic model |
| LLM doesn't understand entity relationships | LLM knows from ontology that mortgages belong to customers |
| Results may be semantically incorrect | Results match the business definitions |

The three layers give the LLM a **grounded, structured understanding** of your data domain — not just raw schema introspection, but the *meaning* behind tables and columns.

---

## Extending

- **More tables**: add a YAML file to `data_model/`, a matching block to `semantic_models/`, and an entity to `ontology/` with a `maps_to` pointing at it.
- **Formal ontology**: treat `ontology/*.yaml` as a lightweight bridge and maintain the canonical ontology in OWL/RDF (e.g. via Protege).
- **Other semantic engines**: the MetricFlow syntax also maps to Cube's schema format or Malloy source/view definitions.
- **Drift detection**: extend `validate_mapping.py` to query `INFORMATION_SCHEMA.COLUMNS` and diff against `data_model/*.yaml`.
