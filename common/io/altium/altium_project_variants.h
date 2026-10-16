/*
 * This program source code file is part of KiCad, a free EDA CAD application.
 *
 * Copyright The KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, you may find one here:
 * http://www.gnu.org/licenses/old-licenses/gpl-2.0.html
 * or you may search the http://www.gnu.org website for the version 2 license,
 * or you may write to the Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA
 */

#ifndef ALTIUM_PROJECT_VARIANTS_H
#define ALTIUM_PROJECT_VARIANTS_H

#include <map>
#include <vector>
#include <wx/string.h>

#include <kiid.h>

/**
 * A single component variation within an Altium project variant.
 *
 * In Altium, each variant can override individual components. Kind=1 means
 * "Not Fitted" (DNP), Kind=0 means "Alternate" with optional field overrides
 * supplied via the AlternatePart string.
 */
struct ALTIUM_VARIANT_ENTRY
{
    wxString                         designator;

    // Component's own unique id (final segment of the Altium path); the value used for matching.
    wxString                         uniqueId;

    int                              kind = 0;
    std::map<wxString, wxString>     alternateFields;
};


/**
 * A project-level assembly variant parsed from an Altium .PrjPcb file.
 *
 * Each [ProjectVariantN] section in the .PrjPcb becomes one of these.
 */
struct ALTIUM_PROJECT_VARIANT
{
    wxString                                 name;
    wxString                                 description;
    std::vector<ALTIUM_VARIANT_ENTRY>        variations;
};


/**
 * Parse all [ProjectVariantN] sections from an Altium .PrjPcb project file.
 *
 * @param aPrjPcbPath Full path to the .PrjPcb file.
 * @return Vector of project variants with their per-component entries.
 */
std::vector<ALTIUM_PROJECT_VARIANT> ParseAltiumProjectVariants( const wxString& aPrjPcbPath );


/**
 * Parse all [ParameterN] sections from an Altium .PrjPcb project file.
 *
 * Altium stores project-wide special strings (PCB_Revision, Company_Name, ...) here as
 * Name/Value pairs. They are what board text such as ".PCB_Revision" resolves against, so
 * they map to KiCad project text variables.
 *
 * @param aPrjPcbPath Full path to the .PrjPcb file.
 * @return Map of upper-cased parameter name to value, matching the case-insensitive variable
 *         references emitted by AltiumPcbSpecialStringsToKiCadStrings.
 */
std::map<wxString, wxString> ParseAltiumProjectParameters( const wxString& aPrjPcbPath );


/**
 * Derive a stable KIID from an Altium component unique id.
 *
 * Altium unique ids are not hexadecimal, so KIID's string constructor cannot parse them and
 * returns a fresh random uuid each call. This maps equal id strings to equal KIIDs.
 *
 * @param aUniqueId Altium component unique id.
 * @return A KIID that is identical for identical input strings.
 */
KIID AltiumUniqueIdToKiid( const wxString& aUniqueId );


/**
 * The schematic documents an Altium .PrjPcb file lists: the [DocumentN] sections whose
 * DocumentPath is a .SchDoc, in N's order, as absolute paths (relative ones are taken from the
 * project file's folder).
 *
 * @param aPrjPcbPath Full path to the .PrjPcb file.
 */
std::vector<wxString> ParseAltiumProjectSchematics( const wxString& aPrjPcbPath );


/**
 * Altium's net identifier scope (Project Options > Options): which names connect across sheets.
 * The values are those of the .PrjPcb's HierarchyMode key.
 */
enum class ALTIUM_NET_SCOPE
{
    AUTOMATIC = 0,           ///< HIERARCHICAL with sheet entries, else FLAT with ports, else GLOBAL
    FLAT = 1,                ///< ports connect across all sheets, net labels stay on their sheet
    HIERARCHICAL = 2,        ///< ports connect only to their sheet entry, power ports are global
    GLOBAL = 3,              ///< ports and net labels connect across all sheets
    STRICT_HIERARCHICAL = 4  ///< as HIERARCHICAL, but power ports stay on their sheet
};


/**
 * The net identifier scope of an Altium .PrjPcb file (AUTOMATIC when it sets none).
 *
 * @param aPrjPcbPath Full path to the .PrjPcb file.
 */
ALTIUM_NET_SCOPE ParseAltiumProjectNetScope( const wxString& aPrjPcbPath );

#endif // ALTIUM_PROJECT_VARIANTS_H
