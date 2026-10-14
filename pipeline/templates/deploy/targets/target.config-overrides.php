<?php
// @@HEADER@@
//
// Settings for the "@@ENV_NAME@@" environment only, added to app/etc/env.php
// on the box (deploy/scripts/render-env). The pipeline wizard writes this file
// once and never changes it again — it is yours to edit.
//
// Use it to switch off what must never run outside production on a copy of
// the live database: your ERP and marketing connectors, feeds, webhooks.
// Values here win over the database (Magento reads env.php's 'system' first).
// Example:
//
//   'system' => ['default' => [
//       'acme_erp' => ['general' => ['enabled' => '0']],
//       'newsletter' => ['general' => ['active' => '0']],
//   ]],
//
// On a non-production environment the kit already turns off outgoing email,
// asks search engines not to index the store, and puts PayPal and Braintree
// in sandbox mode.
return [
    'system' => ['default' => []],
];
