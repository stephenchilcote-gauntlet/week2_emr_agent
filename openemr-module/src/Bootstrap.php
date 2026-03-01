<?php

/**
 * Clinical Assistant Sidebar module event wiring
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    OpenEMR Community
 * @copyright Copyright (c) 2026 OpenEMR
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\ClinicalAssistant;

use OpenEMR\Common\Utils\CacheUtils;
use OpenEMR\Events\Core\StyleFilterEvent;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;

class Bootstrap
{
    private const EMBED_SCRIPT = '/interface/modules/custom_modules/oe-module-clinical-assistant/public/assets/embed.js';

    public function subscribeToEvents(EventDispatcherInterface $eventDispatcher): void
    {
        $eventDispatcher->addListener(StyleFilterEvent::EVENT_NAME, $this->injectEmbedScript(...));
    }

    public function injectEmbedScript(StyleFilterEvent $event): void
    {
        $styles = $event->getStyles();
        $script = '<script src="' . CacheUtils::addAssetCacheParamToPath(self::EMBED_SCRIPT) . '"></script>';
        if (!in_array($script, $styles, true)) {
            $styles[] = $script;
            $event->setStyles($styles);
        }
    }
}
