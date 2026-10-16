/*
 * This program source code file is part of KiCad, a free EDA CAD application.
 *
 * Copyright The KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation, either version 3 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#ifndef PLUGIN_COMMON_CHOOSE_PROJECT_H
#define PLUGIN_COMMON_CHOOSE_PROJECT_H

#include <functional>
#include <map>
#include <vector>
#include <reporter.h>
#include <wx/intl.h>
#include <wx/string.h>

/**
 * @brief Describes how non-KiCad boards and schematics should be imported as KiCad projects
 */
struct IMPORT_PROJECT_DESC
{
    wxString ComboName;
    wxString PCBName;
    wxString SchematicName;

    wxString ComboId;
    wxString PCBId;
    wxString SchematicId;

    IMPORT_PROJECT_DESC() {}
};

/**
 * @brief Pointer to a function that takes descriptions of the source projects
 * and removes the ones that are not needed, or clears their ID fields.
 */
using CHOOSE_PROJECT_HANDLER = std::function<std::vector<IMPORT_PROJECT_DESC>( const std::vector<IMPORT_PROJECT_DESC>& )>;


/**
 * @brief The choice made with nobody to ask (kicad-cli, or no handler registered): the first
 * project offered, which is a board's schematic + PCB pair when the source has one.
 *
 * @param aReporter if given, told which project was imported and which were left out.
 */
inline CHOOSE_PROJECT_HANDLER ChooseFirstProject( REPORTER* aReporter = nullptr )
{
    return [aReporter]( const std::vector<IMPORT_PROJECT_DESC>& aProjects )
    {
        if( aProjects.empty() )
            return aProjects;

        if( aReporter )
        {
            auto describe = []( const IMPORT_PROJECT_DESC& aDesc ) -> wxString
            {
                if( !aDesc.ComboName.empty() )
                    return wxString::Format( _( "board '%s'" ), aDesc.ComboName );

                if( !aDesc.SchematicName.empty() )
                    return wxString::Format( _( "schematic '%s'" ), aDesc.SchematicName );

                return wxString::Format( _( "PCB '%s'" ), aDesc.PCBName );
            };

            wxString others;

            for( size_t i = 1; i < aProjects.size(); ++i )
                others << ( i > 1 ? wxS( ", " ) : wxS( "" ) ) << describe( aProjects[i] );

            aReporter->Report( wxString::Format( _( "The source holds %d projects; importing the "
                                                    "first, %s (left out: %s)\n" ),
                                                 (int) aProjects.size(), describe( aProjects[0] ),
                                                 others ),
                               RPT_SEVERITY_INFO );
        }

        return std::vector<IMPORT_PROJECT_DESC>{ aProjects[0] };
    };
}


/**
 * @brief Plugin class for import plugins that support choosing a project
 */
class PROJECT_CHOOSER_PLUGIN
{
public:
    /**
     * @brief Register a different handler to be called when a non-KiCad project
     * contains multiple PCB+Schematic combinations.
     *
     * The function is marked as virtual, so the plugins can implement extra
     * logic (e.g., enable warnings or checks)
     */
    virtual void RegisterCallback( CHOOSE_PROJECT_HANDLER aChooseProjectHandler )
    {
        m_choose_project_handler = aChooseProjectHandler;
    }

    virtual ~PROJECT_CHOOSER_PLUGIN() = default;

protected:
    /// Callback to choose projects to import; the first one until a caller registers another
    CHOOSE_PROJECT_HANDLER m_choose_project_handler = ChooseFirstProject();
};

#endif // PLUGIN_COMMON_CHOOSE_PROJECT_H
