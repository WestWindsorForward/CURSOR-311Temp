/**
 * Google provider registration.
 *
 * This module (and everything it imports, including
 * @googlemaps/markerclusterer) is only pulled in when a town has selected
 * Google — src/maps/registry.ts reaches it through a dynamic import.
 */

import { loadGoogleMaps } from '../../../utils/googleMaps';
import {
    GeocodingProvider,
    MapInitOptions,
    MapProviderConfig,
    MapProviderFactory,
    MapRenderer,
} from '../../types';
import { createGoogleGeocoder } from './geocoder';
import { GOOGLE_CAPABILITIES, GoogleMapRenderer } from './renderer';

export const googleMapProvider: MapProviderFactory = {
    id: 'google',
    displayName: 'Google Maps',
    capabilities: GOOGLE_CAPABILITIES,

    load(config: MapProviderConfig): Promise<void> {
        if (!config.apiKey) return Promise.reject(new Error('Google Maps API key is required'));
        return loadGoogleMaps(config.apiKey);
    },

    createRenderer(container: HTMLElement, _config: MapProviderConfig, options: MapInitOptions): MapRenderer {
        return new GoogleMapRenderer(container, options);
    },

    createGeocoder(_config: MapProviderConfig): GeocodingProvider {
        return createGoogleGeocoder();
    },

    /**
     * `gm_authFailure` is Google's documented way of saying "this key is not
     * allowed from this site". It is a global the SDK *calls*: it does not
     * throw, does not reject the loader, and does not fail map construction.
     * The map object comes back intact and the tiles never arrive -- which is
     * the grey map, and the reason nothing else here notices.
     *
     * Chained rather than replaced, and put back by `stop()`. A hook left
     * behind would route every later failure into a check that has finished.
     *
     * Quota is the one failure this hook does not reliably see. Google fires
     * `gm_authFailure` for key problems (InvalidKey, RefererNotAllowed,
     * expired billing); an exhausted quota (OverQuotaMapError) instead logs to
     * the console and paints Google's own "Sorry! Something went wrong"
     * overlay inside the map div -- the page around it keeps working. That is
     * an acceptable resident experience because LocationPicker's address box
     * does not depend on the map canvas: its geocoder chain asks our backend
     * first (see LocationPicker's chainGeocoders call), and the backend falls
     * through to OpenStreetMap when Google is over quota. The *admin* flag for
     * that state also comes from the backend: a project- or key-wide cap 429s
     * the server-side geocoder too, which records it on the `maps` health row
     * (geocode_dispatch + connector_health.note_quota_failure). Only a cap set
     * on the Maps JavaScript API alone stays invisible to the server, and that
     * is a limitation to know about, not one the browser can reliably report
     * -- there is no documented hook for OverQuotaMapError.
     */
    watchAuthFailure() {
        type Host = { gm_authFailure?: (() => void) | undefined };
        const host = window as unknown as Host;
        const previous = host.gm_authFailure;
        let failed = false;

        host.gm_authFailure = () => {
            failed = true;
            try { previous?.(); } catch { /* not ours to care about */ }
        };

        return {
            failed: () => failed,
            stop: () => { host.gm_authFailure = previous; },
        };
    },
};
