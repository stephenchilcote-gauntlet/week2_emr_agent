<?php

/**
 * Clinical Assistant Sidebar module bootstrap
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    OpenEMR Community
 * @copyright Copyright (c) 2026 OpenEMR
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\ClinicalAssistant;

use OpenEMR\Common\Utils\CacheUtils;

/**
 * @global OpenEMR\Core\ModulesClassLoader $classLoader
 */
$classLoader->registerNamespaceIfNotExists('OpenEMR\\Modules\\ClinicalAssistant\\', __DIR__ . DIRECTORY_SEPARATOR . 'src');

// Register output buffer to inject embed script on all pages.
// We use ob_start rather than ScriptFilterEvent because it catches every page
// (including those that don't call Header::setupHeader()) and injects at
// </body> where the DOM is already built.
$embedScript = '/interface/modules/custom_modules/oe-module-clinical-assistant/public/assets/embed.js';
ob_start(function($buffer) use ($embedScript) {
    $scriptTag = '<script src="' . CacheUtils::addAssetCacheParamToPath($embedScript) . '"></script>';
    if (strpos($buffer, '</body>') !== false) {
        $buffer = str_replace('</body>', $scriptTag . "\n</body>", $buffer);
    }
    return $buffer;
}, PHP_OUTPUT_HANDLER_FLUSHABLE | PHP_OUTPUT_HANDLER_CLEANABLE);
