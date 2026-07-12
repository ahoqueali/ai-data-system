-- Running balance of a current account as of 10 days ago
-- Replace '<ACCOUNT_ID>' with the actual current_account_id

SELECT
  t.transaction_id,
  t.transaction_date,
  t.transaction_type,
  t.amount,
  t.balance_after AS running_balance
FROM
  `your_gcp_project.core.transaction` t
WHERE
  t.account_id = '<ACCOUNT_ID>'
  AND t.transaction_date <= DATE_SUB(CURRENT_DATE(), INTERVAL 10 DAY)
  AND t.status = 'posted'
ORDER BY
  t.transaction_date ASC,
  t.transaction_id ASC;
