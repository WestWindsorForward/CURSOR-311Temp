import React, { useEffect, useRef, useState, useCallback } from 'react';
import { MapPin, Crosshair, Loader2 } from 'lucide-react';
import { MapLayer } from '../services/api';
import {
    AddressSuggestion,
    AutocompleteHandle,
    GeocodingProvider,
    LatLng,
    MapRenderer,
    MarkerHandle,
    MarkerOptions,
    assetIcon,
    backendGeocodingProvider,
    boundsOfGeoJson,
    clusterStyle,
    locationPinIcon,
    chainGeocoders,
    createGeocoder,
    CONTINENTAL_US_CENTER,
    createMap,
    extractFeatures,
    firstPolygonRings,
    isPointInGeoJson,
    MapProviderConfig,
    hasMapCredential,
    el,
    popupRoot,
} from '../maps';

interface LocationPickerProps {
    /**
     * The town's chosen provider and only that provider's credentials.
     * Built once per page with resolveMapProviderConfig(); components must not
     * assemble their own, which is how every map silently defaulted to Google.
     */
    config: MapProviderConfig;
    townshipBoundary?: object | null; // GeoJSON boundary from OpenStreetMap
    customLayers?: MapLayer[]; // Custom GeoJSON layers (parks, storm drains, etc.)
    defaultCenter?: { lat: number; lng: number };
    defaultZoom?: number;
    value?: { address: string; lat: number | null; lng: number | null };
    onChange: (location: { address: string; lat: number | null; lng: number | null }) => void;
    onAssetSelect?: (asset: { layerName: string; properties: Record<string, any>; lat: number; lng: number }) => void;
    onOutOfBounds?: () => void; // Called when pin is placed outside boundary
    placeholder?: string;
    className?: string;
}

// The dropped pin and the cluster bubbles both come from src/maps/markerIcons,
// which is the only place any map glyph is described. They used to be defined
// here, and separately again in RequestDetailMap and StaffDashboardMap -- three
// cluster styles, three asset markers, no two maps looking alike.
export default function LocationPicker({
    config,
    townshipBoundary,
    customLayers = [],
    defaultCenter = CONTINENTAL_US_CENTER,
    defaultZoom = 17,
    value,
    onChange,
    onAssetSelect,
    onOutOfBounds,
    placeholder = 'Search for an address...',
    className = '',
}: LocationPickerProps) {

    const mapContainerRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLInputElement>(null);
    const mapRef = useRef<MapRenderer | null>(null);
    const markerRef = useRef<MarkerHandle | null>(null);
    const geocoderRef = useRef<GeocodingProvider | null>(null);
    const autocompleteRef = useRef<AutocompleteHandle | null>(null);
    // Our own suggestion list, used when the provider has no widget of its own.
    //
    // Google's legacy Autocomplete decorated this input directly. Its
    // replacement is a custom element that replaces the input, and providers
    // other than Google have no widget at all -- so `attachAutocomplete`
    // returning null is a normal answer, and the interface has always said
    // callers should fall back to suggest() plus their own list. Nothing
    // implemented that half, so "no widget" meant "no autocomplete".
    const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([]);
    const [suggestOpen, setSuggestOpen] = useState(false);
    const [activeSuggestion, setActiveSuggestion] = useState(-1);
    const [needsOwnList, setNeedsOwnList] = useState(false);
    const suggestSeq = useRef(0);

    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [inputValue, setInputValue] = useState(value?.address || '');
    const [isLocating, setIsLocating] = useState(false);
    const [isOutOfBounds, setIsOutOfBounds] = useState(false);

    // A map that could not load used to replace this whole component with a red
    // box -- no address field, so a resident hit by a rejected or capped key
    // could not file a report at all, which is the opposite of failing
    // gracefully. The canvas is the only part that goes; the address box stays
    // and keeps resolving through the backend.
    const mapUnavailable = !!error;

    // Sync input value with external value ONLY when address changes from parent
    useEffect(() => {
        if (value?.address !== undefined && value.address !== inputValue && value.address !== '') {
            setInputValue(value.address);
        }
    }, [value?.address]);

    // Backend first (it meters geocoding for the cost report), provider SDK second.
    
    // Re-center, fit bounds, and display boundary whenever townshipBoundary prop changes
    useEffect(() => {
        const map = mapRef.current;
        if (!map || !townshipBoundary) return;

        try {
            const rings = firstPolygonRings(townshipBoundary);
            if (rings.length > 0) {
                map.addPolygon({
                    paths: rings,
                    style: {
                        fillColor: "#6366f1",
                        fillOpacity: 0.12,
                        strokeColor: "#6366f1",
                        strokeWidth: 3,
                        strokeOpacity: 1,
                        clickable: false,
                    },
                });
            } else {
                map.addGeoJsonLayer({
                    data: townshipBoundary,
                    style: {
                        fillColor: "#6366f1",
                        fillOpacity: 0.12,
                        strokeColor: "#6366f1",
                        strokeWidth: 3,
                        strokeOpacity: 1,
                        clickable: false,
                    },
                });
            }
        } catch (e) {
            console.warn("Failed to add updated township boundary:", e);
        }

        const geojson = townshipBoundary as any;
        if (geojson?.center?.lat && geojson?.center?.lng) {
            map.panTo({ lat: geojson.center.lat, lng: geojson.center.lng });
        }

        const boundaryBounds = boundsOfGeoJson(townshipBoundary);
        if (boundaryBounds) {
            map.fitBounds(boundaryBounds);
        }
    }, [townshipBoundary]);

    const reverseGeocode = useCallback(async (lat: number, lng: number): Promise<string> => {
        try {
            const result = await geocoderRef.current?.reverseGeocode({ lat, lng });
            if (result?.formattedAddress) return result.formattedAddress;
        } catch (error) {
            console.warn('Reverse geocode failed:', error);
        }
        return `${lat.toFixed(6)}, ${lng.toFixed(6)}`;
    }, []);

    // Place marker on map with a visible custom pin
    const placeMarker = useCallback((position: LatLng) => {
        const map = mapRef.current;
        if (!map) return;

        // Check if position is within boundary
        const inBounds = isPointInGeoJson(position.lat, position.lng, townshipBoundary);
        setIsOutOfBounds(!inBounds);

        if (!inBounds && onOutOfBounds) {
            onOutOfBounds();
        }

        if (markerRef.current) {
            markerRef.current.setPosition(position);
        } else {
            markerRef.current = map.addMarker({
                position,
                draggable: true,
                dropAnimation: true,
                icon: locationPinIcon(),
                onDragEnd: async (dropped) => {
                    const stillInBounds = isPointInGeoJson(dropped.lat, dropped.lng, townshipBoundary);
                    setIsOutOfBounds(!stillInBounds);

                    if (!stillInBounds && onOutOfBounds) {
                        onOutOfBounds();
                    }

                    const address = await reverseGeocode(dropped.lat, dropped.lng);
                    setInputValue(address);
                    onChange({ address, lat: dropped.lat, lng: dropped.lng });
                },
            });
        }

        // Center map on marker
        map.panTo(position);
    }, [onChange, reverseGeocode, townshipBoundary, onOutOfBounds]);

    // Initialize the map
    useEffect(() => {
        // The address box is wired to the backend geocoder before anything
        // touches the provider SDK, so a key that is rejected, blocked or over
        // its cap costs the resident the canvas and nothing else. The backend
        // falls through to OpenStreetMap on its own side, which is why this
        // still resolves addresses while the town's own provider is refusing
        // calls. It is upgraded to the full chain once the map is up.
        geocoderRef.current = backendGeocodingProvider;

        if (!hasMapCredential(config)) {
            setError('No map provider is configured yet.');
            setNeedsOwnList(true);
            setIsLoading(false);
            return;
        }

        let isMounted = true;

        const initMap = async () => {
            try {
                if (!mapContainerRef.current || !inputRef.current) return;

                // Determine map center: value > boundary center > default
                let mapCenter = defaultCenter;

                const geojson = townshipBoundary as any;
                if (geojson?.center?.lat && geojson?.center?.lng) {
                    mapCenter = { lat: geojson.center.lat, lng: geojson.center.lng };
                }

                // Value takes precedence if coordinates are set
                if (value?.lat && value?.lng) {
                    mapCenter = { lat: value.lat, lng: value.lng };
                }

                const map = await createMap(mapContainerRef.current, config, {
                    center: mapCenter,
                    zoom: defaultZoom,
                    baseMapType: 'hybrid', // Satellite with labels
                    controls: {
                        baseMapSwitcher: {
                            enabled: true,
                            position: 'top-right',
                            types: ['roadmap', 'satellite', 'hybrid'],
                        },
                        streetView: { enabled: false },
                        fullscreen: { enabled: true, position: 'right-bottom' },
                        zoom: { enabled: true, position: 'right-center' },
                    },
                });

                if (!isMounted) {
                    map.destroy();
                    return;
                }
                mapRef.current = map;

                geocoderRef.current = chainGeocoders(
                    backendGeocodingProvider,
                    await createGeocoder(config),
                );

                // Add township boundary overlay if GeoJSON is provided
                if (townshipBoundary) {
                    try {
                        // Ring 0 is the outer boundary and the rest are holes;
                        // drawing them as one polygon is what renders the holes.
                        const rings = firstPolygonRings(townshipBoundary);

                        if (rings.length > 0) {
                            map.addPolygon({
                                paths: rings,
                                style: {
                                    fillColor: '#6366f1',
                                    fillOpacity: 0.1,
                                    strokeColor: '#6366f1',
                                    strokeWidth: 3,
                                    strokeOpacity: 1,
                                    clickable: false,
                                },
                            });
                        } else {
                            // Fallback: hand the GeoJSON to a data layer as-is.
                            map.addGeoJsonLayer({
                                data: townshipBoundary,
                                style: {
                                    fillColor: '#6366f1',
                                    fillOpacity: 0.1,
                                    strokeColor: '#6366f1',
                                    strokeWidth: 3,
                                    strokeOpacity: 1,
                                    clickable: false,
                                },
                            });
                        }
                    } catch (e) {
                        console.warn('Failed to add township boundary:', e);
                    }

                    // Fit map to boundary bounds (if no existing pin location)
                    const boundaryBounds = boundsOfGeoJson(townshipBoundary);
                    if (boundaryBounds) map.fitBounds(boundaryBounds);
                }

                // Render custom layers (parks, storm drains, utilities, etc.)
                // Points = visible markers (pucks), Polygons = not rendered here
                const layerFeaturesRef: Array<{
                    layer: MapLayer;
                    properties: Record<string, any>;
                    geometry: LatLng;
                }> = [];

                if (customLayers && customLayers.length > 0) {
                    const popup = map.createPopup();
                    const assetMarkers: MarkerOptions[] = [];

                    customLayers.forEach((layer) => {
                        try {
                            if (!layer.geojson) return;

                            extractFeatures(layer.geojson).forEach((feature) => {
                                if (feature.geometryType !== 'Point' || !feature.position) return;

                                const props = feature.properties as Record<string, any>;
                                const position = feature.position;

                                assetMarkers.push({
                                    position,
                                    icon: assetIcon(layer.fill_color, layer.stroke_color),
                                    title: props.name || props.asset_id || layer.name,
                                    onClick: (_e, marker) => {
                                        const assetName = props.name || layer.name;
                                        const assetType = props.asset_type
                                            ? String(props.asset_type).replace(/_/g, ' ')
                                            : layer.layer_type || 'asset';

                                        const selectAsset = () => {
                                            markerRef.current?.setPosition(position);
                                            map.panTo(position);
                                            onAssetSelect?.({
                                                layerName: layer.name,
                                                properties: props,
                                                lat: position.lat,
                                                lng: position.lng,
                                            });
                                            reverseGeocode(position.lat, position.lng).then(address => {
                                                onChange({ address, lat: position.lat, lng: position.lng });
                                            });
                                            popup.close();
                                        };

                                        // Built as DOM, not markup. Asset names and
                                        // property values come from an uploaded GeoJSON,
                                        // and the buttons are real listeners rather than
                                        // onclick attributes reaching for generated window
                                        // globals -- which leaked one per rendered feature.
                                        popup.setContent(popupRoot('padding: 2px 4px 8px 8px; min-width: 180px; max-width: 260px;', [
                                            el('div', {
                                                style: 'display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; margin-bottom: 4px;',
                                                children: [
                                                    el('div', {
                                                        style: 'font-size: 14px; font-weight: 700; color: #0f172a; line-height: 1.2; flex: 1;',
                                                        text: String(assetName),
                                                    }),
                                                    el('button', {
                                                        style: 'background: none; border: none; cursor: pointer; padding: 0; color: #94a3b8; font-size: 18px; line-height: 1;',
                                                        text: '\u00D7',
                                                        title: 'Close',
                                                        onClick: () => popup.close(),
                                                    }),
                                                ],
                                            }),
                                            Object.keys(props).filter(k => k !== 'name').length > 0 && el('div', {
                                                style: 'margin-bottom: 6px; border-top: 1px solid #e2e8f0; padding-top: 4px;',
                                                children: Object.entries(props)
                                                    .filter(([key]) => key !== 'name')
                                                    .map(([key, val]) => el('div', {
                                                        style: 'display: flex; justify-content: space-between; gap: 12px; padding: 4px 0; border-bottom: 1px solid #e2e8f0;',
                                                        children: [
                                                            el('span', {
                                                                style: 'color: #64748b; font-size: 12px; text-transform: capitalize;',
                                                                text: key.replace(/_/g, ' '),
                                                            }),
                                                            el('span', {
                                                                style: 'font-size: 12px; color: #334155; font-weight: 500; text-align: right;',
                                                                text: String(val),
                                                            }),
                                                        ],
                                                    })),
                                            }),
                                            el('button', {
                                                style: 'width: 100%; padding: 6px 10px; background: #8b5cf6; color: white; border: none; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; text-transform: capitalize;',
                                                text: `\u{1F4CD} Select this ${assetType}`,
                                                onClick: selectAsset,
                                            }),
                                        ]));
                                        popup.openAt(marker);
                                    },
                                });

                                // Store for proximity detection
                                layerFeaturesRef.push({ layer, properties: props, geometry: position });
                            });
                        } catch (e) {
                            console.warn(`Failed to render layer ${layer.name}:`, e);
                        }
                    });

                    // Store the features ref for proximity detection
                    (window as any).__mapLayerFeatures = layerFeaturesRef;

                    if (assetMarkers.length > 0) {
                        const assetLayer = map.createMarkerLayer({
                            cluster: { style: clusterStyle },
                        });
                        assetLayer.setMarkers(assetMarkers);
                    }
                }

                // Provider-native address autocomplete on the search input. The
                // map feeds it viewport bias; the geocoder never sees the map.
                const autocomplete = geocoderRef.current.attachAutocomplete?.(inputRef.current, {
                    // Deliberately NOT addressesOnly. Residents do not report a
                    // pothole at a street address, they report it at Memorial
                    // Park, or by the library, or at an intersection -- and an
                    // addresses-only filter excludes every one of those. With
                    // the town's viewport as bias, "memorial" returned nothing
                    // at all while the unfiltered search returned Memorial Park
                    // two towns over, which is the answer somebody wanted.
                    countries: ['us'],
                    biasBounds: map.getBounds(),
                    onSelect: (place) => {
                        setInputValue(place.formattedAddress);
                        onChange({ address: place.formattedAddress, lat: place.position.lat, lng: place.position.lng });

                        if (place.viewport) {
                            map.fitBounds(place.viewport, { maxZoom: 19 });
                        } else {
                            map.setCenter(place.position);
                            map.setZoom(19);
                        }

                        placeMarker(place.position);
                    },
                }) ?? null;
                autocompleteRef.current = autocomplete;
                setNeedsOwnList(!autocomplete);

                if (autocomplete) {
                    map.on('idle', () => autocomplete.setBiasBounds(map.getBounds()));
                }

                // Handle map clicks for precise location selection
                map.on('click', async ({ position }) => {
                    placeMarker(position);

                    const address = await reverseGeocode(position.lat, position.lng);
                    setInputValue(address);
                    onChange({ address, lat: position.lat, lng: position.lng });
                });

                // Place initial marker if value exists
                if (value?.lat && value?.lng) {
                    placeMarker({ lat: value.lat, lng: value.lng });
                }

                setIsLoading(false);
            } catch (err) {
                if (isMounted) {
                    setError('Failed to load the map');
                    // No provider widget attached, so this component's own
                    // suggestion list is the only autocomplete left.
                    setNeedsOwnList(true);
                    setIsLoading(false);
                }
            }
        };

        initMap();

        return () => {
            isMounted = false;
            autocompleteRef.current?.destroy();
            autocompleteRef.current = null;
            markerRef.current = null;
            mapRef.current?.destroy();
            mapRef.current = null;
        };
    }, [config.provider, config.apiKey, config.styleId]); // Only re-init when the provider or its credentials change

    // Update marker position when value changes from parent
    useEffect(() => {
        if (mapRef.current && value?.lat && value?.lng && !isLoading) {
            placeMarker({ lat: value.lat, lng: value.lng });
        }
    }, [value?.lat, value?.lng, isLoading, placeMarker]);

    // Handle "Use my location" button
    const handleUseMyLocation = () => {
        if (!navigator.geolocation) {
            alert("Geolocation is not supported by your browser");
            return;
        }

        setIsLocating(true);

        navigator.geolocation.getCurrentPosition(
            async (position) => {
                const lat = position.coords.latitude;
                const lng = position.coords.longitude;

                if (mapRef.current) {
                    mapRef.current.setCenter({ lat, lng });
                    mapRef.current.setZoom(19);
                    placeMarker({ lat, lng });

                    const address = await reverseGeocode(lat, lng);
                    setInputValue(address);
                    onChange({ address, lat, lng });
                }

                setIsLocating(false);
            },
            (error) => {
                console.error('Geolocation error:', error);
                alert("Unable to get your location. Please enter an address manually.");
                setIsLocating(false);
            },
            { enableHighAccuracy: true, timeout: 10000 }
        );
    };

    // Handle manual input changes - only update local state, not parent
    const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const next = e.target.value;
        setInputValue(next);
        // Don't call onChange here - only update when:
        // 1. User selects from autocomplete dropdown
        // 2. User clicks on the map
        // 3. User drags the marker
        // 4. User uses "my location" button
        //
        // With no map, none of those four can happen: the typed text is all the
        // resident can give, so it has to reach the form directly or a map
        // failure swallows the location on submit. Coordinates go with it --
        // the ones on screen belonged to the address that was just replaced.
        if (mapUnavailable) onChange({ address: next, lat: null, lng: null });
        if (needsOwnList) requestSuggestions(next);
    };

    /**
     * Ask the geocoder chain what this might be.
     *
     * Sequence-numbered rather than debounced-and-hoped: two keystrokes can be
     * in flight at once and the slower one must not overwrite the newer answer,
     * which shows up as the list flicking back to what you typed a moment ago.
     */
    const requestSuggestions = useCallback((query: string) => {
        const geocoder = geocoderRef.current;
        if (!geocoder?.suggest || query.trim().length < 3) {
            setSuggestions([]);
            setSuggestOpen(false);
            return;
        }
        const seq = ++suggestSeq.current;
        geocoder.suggest(query, {
            // See the note on attachAutocomplete above: a 311 location is as
            // often a park or a landmark as it is a street address.
            countries: ['us'],
            biasBounds: mapRef.current?.getBounds() ?? null,
        }).then(results => {
            if (seq !== suggestSeq.current) return;
            setSuggestions(results);
            setSuggestOpen(results.length > 0);
            setActiveSuggestion(-1);
        }).catch(() => {
            if (seq !== suggestSeq.current) return;
            setSuggestions([]);
            setSuggestOpen(false);
        });
    }, []);

    const chooseSuggestion = useCallback(async (suggestion: AddressSuggestion) => {
        const geocoder = geocoderRef.current;
        setSuggestOpen(false);
        setSuggestions([]);
        if (!geocoder) return;

        // Resolve through the chain, then fall back to geocoding the label --
        // a provider without resolveSuggestion still gives a usable label.
        let place = geocoder.resolveSuggestion
            ? await geocoder.resolveSuggestion(suggestion).catch(() => null)
            : null;
        if (!place) {
            const results = await geocoder.geocode(suggestion.label).catch(() => []);
            place = results[0] ?? null;
        }
        if (!place) return;

        setInputValue(place.formattedAddress);
        onChange({ address: place.formattedAddress, lat: place.position.lat, lng: place.position.lng });

        const map = mapRef.current;
        if (map) {
            if (place.viewport) map.fitBounds(place.viewport, { maxZoom: 19 });
            else { map.setCenter(place.position); map.setZoom(19); }
        }
        placeMarker(place.position);
    }, [onChange, placeMarker]);

    const handleInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (!suggestOpen || !suggestions.length) return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setActiveSuggestion(i => (i + 1) % suggestions.length);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActiveSuggestion(i => (i <= 0 ? suggestions.length - 1 : i - 1));
        } else if (e.key === 'Enter') {
            // Only when something is highlighted. Otherwise Enter belongs to
            // the form the picker sits in.
            if (activeSuggestion >= 0) {
                e.preventDefault();
                void chooseSuggestion(suggestions[activeSuggestion]);
            }
        } else if (e.key === 'Escape') {
            setSuggestOpen(false);
        }
    };

    return (
        <div className={`space-y-4 ${className}`}>
            {/* Address Input with Autocomplete */}
            <div className="relative">
                <MapPin className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-white/40 pointer-events-none z-10" />
                <input
                    ref={inputRef}
                    type="text"
                    placeholder={placeholder}
                    value={inputValue}
                    onChange={handleInputChange}
                    onKeyDown={handleInputKeyDown}
                    onBlur={() => window.setTimeout(() => setSuggestOpen(false), 150)}
                    aria-label="Location or address"
                    role={needsOwnList ? 'combobox' : undefined}
                    aria-expanded={needsOwnList ? suggestOpen : undefined}
                    aria-controls={needsOwnList ? 'location-suggestions' : undefined}
                    aria-autocomplete={needsOwnList ? 'list' : undefined}
                    className="w-full h-12 pl-12 pr-14 rounded-xl bg-white/10 border border-white/20 text-white placeholder-white/40 focus:outline-none focus:border-primary-500/50 focus:ring-2 focus:ring-primary-500/20 transition-all"
                    disabled={isLoading}
                    autoComplete="off"
                />
                {/* Use my location button */}
                <button
                    type="button"
                    onClick={handleUseMyLocation}
                    disabled={isLoading || isLocating}
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-2.5 rounded-lg bg-primary-500/20 hover:bg-primary-500/30 transition-colors disabled:opacity-50"
                    aria-label="Use my current location"
                    title="Use my current location"
                >
                    {isLocating ? (
                        <Loader2 className="w-5 h-5 text-primary-400 animate-spin" />
                    ) : (
                        <Crosshair className="w-5 h-5 text-primary-400" />
                    )}
                </button>

                {/* Our own suggestion list. Only rendered when the provider
                    declined to attach its own -- a key with Places (New), a
                    town on Esri or Azure, or the backend/road-data fallback. */}
                {needsOwnList && suggestOpen && suggestions.length > 0 && (
                    <ul
                        id="location-suggestions"
                        role="listbox"
                        className="absolute left-0 right-0 top-full mt-1 z-30 max-h-64 overflow-y-auto rounded-xl border border-white/15 bg-slate-900/95 backdrop-blur shadow-2xl py-1"
                    >
                        {suggestions.map((s, i) => (
                            <li key={s.id} role="option" aria-selected={i === activeSuggestion}>
                                <button
                                    type="button"
                                    // onMouseDown, not onClick: blur fires first
                                    // and would close the list before the click
                                    // ever lands.
                                    onMouseDown={(e) => { e.preventDefault(); void chooseSuggestion(s); }}
                                    onMouseEnter={() => setActiveSuggestion(i)}
                                    className={`w-full text-left px-4 py-2.5 transition-colors ${i === activeSuggestion ? 'bg-primary-500/25' : 'hover:bg-white/10'}`}
                                >
                                    <span className="block text-sm text-white truncate">{s.label}</span>
                                    {s.secondaryLabel && (
                                        <span className="block text-xs text-white/55 truncate">{s.secondaryLabel}</span>
                                    )}
                                </button>
                            </li>
                        ))}
                    </ul>
                )}
            </div>

            {/* Map Container, or what is left when the provider will not draw
                one. Same shape as the resident portal's no-provider fallback:
                say the map is missing, keep the address field working. */}
            {mapUnavailable ? (
                <div
                    className="p-4 rounded-xl bg-white/5 border border-white/10 text-center text-white/40"
                    role="status"
                >
                    <MapPin className="w-8 h-8 mx-auto mb-2 opacity-50" />
                    <p className="text-sm">{error} You can still type the address above.</p>
                </div>
            ) : (
            <div className="relative rounded-2xl overflow-hidden border-2 border-white/10 shadow-xl">
                {isLoading && (
                    <div className="absolute inset-0 flex items-center justify-center bg-slate-900/80 z-10">
                        <div className="text-center">
                            <div className="w-10 h-10 border-3 border-primary-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
                            <p className="text-sm text-white/60">Loading map...</p>
                        </div>
                    </div>
                )}
                <div
                    ref={mapContainerRef}
                    className="w-full h-72 md:h-96"
                    style={{ minHeight: '288px' }}
                />
            </div>
            )}

            {/* Out of bounds warning */}
            {isOutOfBounds && (
                <div className="flex items-center gap-2 text-sm bg-red-500/10 rounded-xl px-4 py-3 border border-red-500/30">
                    <span className="text-red-400">⚠️</span>
                    <span className="text-red-300">
                        This location is outside the municipality boundary. Please select a location within the jurisdiction.
                    </span>
                </div>
            )}

            {/* Instructions or Selected location info - shown BELOW the map */}
            {!value?.lat && !value?.lng && !isLoading && !mapUnavailable ? (
                <div className="flex items-center justify-center gap-2 text-sm bg-white/5 rounded-xl px-4 py-3 border border-white/10">
                    <span className="text-primary-400">📍</span>
                    <span className="text-white/70">Tap the map to select a location, or search above</span>
                </div>
            ) : value?.lat && value?.lng ? (
                <div className="flex flex-col sm:flex-row items-center justify-center gap-1 sm:gap-3 text-sm bg-white/5 rounded-xl px-4 py-3 border border-white/10">
                    <div className="flex items-center gap-2 text-white/60">
                        <MapPin className="w-4 h-4 text-primary-400 flex-shrink-0" />
                        <span className="font-mono text-xs sm:text-sm">
                            {value.lat.toFixed(6)}, {value.lng.toFixed(6)}
                        </span>
                    </div>
                    <span className="hidden sm:block text-white/20">•</span>
                    <span className="text-primary-400 text-xs sm:text-sm font-medium">
                        📍 Drag the pin to fine-tune
                    </span>
                </div>
            ) : null}

        </div>
    );
}
