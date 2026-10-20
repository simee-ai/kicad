/*
 * This program source code file is part of KiCad, a free EDA CAD application.
 *
 * Copyright (C) 2023 Alex Shvartzkop <dudesuchamazing@gmail.com>
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

#ifndef EASYEDAPRO_IMPORT_UTILS_H_
#define EASYEDAPRO_IMPORT_UTILS_H_

#include <set>
#include <wx/stream.h>
#include <wx/string.h>
#include <lib_id.h>
#include <json_common.h>
#include <core/json_serializers.h>

struct IMPORT_PROJECT_DESC;

#define EASY_IT_CONTINUE return false
#define EASY_IT_BREAK return true

namespace EASYEDAPRO
{

wxString ShortenLibName( wxString aProjectName );

LIB_ID ToKiCadLibID( const wxString& aLibName, const wxString& aLibReference );

std::vector<IMPORT_PROJECT_DESC> ProjectToSelectorDialog( const nlohmann::json& aProject,
                                                          bool                  aPcbOnly = false,
                                                          bool                  aSchOnly = false );

/**
 * The projects an EasyEDA Pro project file (.epro, or the older .zip) holds, as the importers offer
 * them to choose from (ProjectToSelectorDialog), all of them even when there is one; none for any
 * other file.
 */
std::vector<IMPORT_PROJECT_DESC> ProjectsOfFile( const wxString& aFileName, bool aPcbOnly,
                                                 bool aSchOnly );

nlohmann::json FindJsonFile( const wxString& aZipFileName, const std::set<wxString>& aFileNames );

nlohmann::json ReadProjectOrDeviceFile( const wxString& aZipFileName );

void IterateZipFiles(
        const wxString&                                                         aFileName,
        std::function<bool( const wxString&, const wxString&, wxInputStream& )> aCallback );

std::vector<nlohmann::json> ParseJsonLines( wxInputStream& aInput, const wxString& aSource );

/**
 * Multiple document types (e.g. footprint and PCB) can be put into a single file, separated by
 * empty line.
 */
std::vector<std::vector<nlohmann::json>> ParseJsonLinesWithSeparation( wxInputStream&  aInput,
                                                                       const wxString& aSource );

/**
 * A JSON object as a map keyed by wxString, its keys read as UTF-8. Converting a JSON object to such a
 * map directly makes each key with wxString's implicit std::string constructor, which uses the C
 * library's locale: in the C locale (kicad-cli in a container) a key it can't encode, such as a Chinese
 * attribute or board name, becomes "", and such keys collapse into one. Use this wherever a key can be
 * user text.
 */
template <typename T>
std::map<wxString, T> ToWxStringMap( const nlohmann::json& aObject )
{
    std::map<wxString, T> result;

    for( auto it = aObject.begin(); it != aObject.end(); ++it )
        result.emplace( wxString::FromUTF8( it.key() ), it.value().template get<T>() );

    return result;
}

/**
 * A JSON object's string and number values as strings (numbers in the C locale), keyed as
 * ToWxStringMap keys them.
 */
std::map<wxString, wxString> AnyMapToStringMap( const nlohmann::json& aInput );

} // namespace EASYEDAPRO


#endif // EASYEDAPRO_IMPORT_UTILS_H_
