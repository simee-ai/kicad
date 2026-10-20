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
#include <limits>
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
 * @brief How a project is named to and by kicad-cli: `board '<name>'` for a board's schematic + PCB
 * pair, else `schematic '<name>'` or `PCB '<name>'`.
 */
inline wxString DescribeProject( const IMPORT_PROJECT_DESC& aDesc )
{
    if( !aDesc.ComboName.empty() )
        return wxString::Format( _( "board '%s'" ), aDesc.ComboName );

    if( !aDesc.SchematicName.empty() )
        return wxString::Format( _( "schematic '%s'" ), aDesc.SchematicName );

    return wxString::Format( _( "PCB '%s'" ), aDesc.PCBName );
}


/**
 * @brief The name alone of a project, as DescribeProject() quotes it.
 */
inline wxString ProjectName( const IMPORT_PROJECT_DESC& aDesc )
{
    if( !aDesc.ComboName.empty() )
        return aDesc.ComboName;

    return !aDesc.SchematicName.empty() ? aDesc.SchematicName : aDesc.PCBName;
}


/**
 * @brief Every project of a list as DescribeProject() names them, joined by ", ", but the one at
 * \a aSkip.
 */
inline wxString DescribeProjects( const std::vector<IMPORT_PROJECT_DESC>& aProjects,
                                  size_t aSkip = std::numeric_limits<size_t>::max() )
{
    wxString list;

    for( size_t i = 0; i < aProjects.size(); ++i )
    {
        if( i == aSkip )
            continue;

        list << ( list.empty() ? wxS( "" ) : wxS( ", " ) ) << DescribeProject( aProjects[i] );
    }

    return list;
}


/**
 * @brief Tell \a aReporter which project of several is imported, and which are left out.
 */
inline void ReportProjectChosen( REPORTER* aReporter, const std::vector<IMPORT_PROJECT_DESC>& aProjects,
                                 size_t aChosen )
{
    if( !aReporter )
        return;

    wxString chosen = DescribeProject( aProjects[aChosen] );

    if( aChosen == 0 )
        chosen = wxString::Format( _( "the first, %s" ), chosen );

    aReporter->Report( wxString::Format( _( "The source holds %d projects; importing %s "
                                            "(left out: %s)\n" ),
                                         (int) aProjects.size(), chosen,
                                         DescribeProjects( aProjects, aChosen ) ),
                       RPT_SEVERITY_INFO );
}


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

        ReportProjectChosen( aReporter, aProjects, 0 );
        return std::vector<IMPORT_PROJECT_DESC>{ aProjects[0] };
    };
}


/**
 * @brief Tell \a aReporter the projects a source holds (`--list-projects`), a `Project: ` line each
 * as DescribeProject() names them; with none, that it holds one, there being nothing to choose.
 */
inline void ReportProjects( REPORTER* aReporter, const std::vector<IMPORT_PROJECT_DESC>& aProjects )
{
    if( aProjects.empty() )
    {
        aReporter->Report( _( "The source holds one project\n" ), RPT_SEVERITY_INFO );
        return;
    }

    aReporter->Report( wxString::Format( wxPLURAL( "The source holds %d project:\n",
                                                   "The source holds %d projects:\n",
                                                   aProjects.size() ),
                                         (int) aProjects.size() ),
                       RPT_SEVERITY_INFO );

    for( const IMPORT_PROJECT_DESC& desc : aProjects )
        aReporter->Report( wxString::Format( _( "Project: %s\n" ), DescribeProject( desc ) ),
                           RPT_SEVERITY_INFO );
}


/**
 * @brief kicad-cli's choice of project, for `sch import` and `pcb import` of a source holding
 * several (EasyEDA Pro): the one named by `--project`, else the first.
 *
 * A project is named as DescribeProject() names it, or by its name alone when no other project of
 * the source has that name. A board is the same choice for `sch import` (its schematic) and
 * `pcb import` (its PCB). An importer only asks when there is a choice: a source holding a single
 * schematic (or PCB) is imported whatever is named.
 */
class CLI_PROJECT_CHOICE
{
public:
    CLI_PROJECT_CHOICE( const wxString& aName, REPORTER* aReporter ) :
            m_name( aName ),
            m_reporter( aReporter )
    {}

    /// The handler to register with the importer; it refers to this object, which must outlive it.
    CHOOSE_PROJECT_HANDLER Handler()
    {
        return [this]( const std::vector<IMPORT_PROJECT_DESC>& aProjects )
        {
            return choose( aProjects );
        };
    }

    /// The importer offered a choice of projects.
    bool Asked() const { return m_asked; }

    /// No project, or more than one, has the name asked for; the reporter was told which there are.
    bool Failed() const { return m_failed; }

    /// Once the importer ran: if a project was named but there was none to choose, say so.
    void ReportUnasked() const
    {
        if( !m_asked && !m_name.empty() )
            m_reporter->Report( wxString::Format( _( "The source holds one project; importing it, "
                                                     "though %s was asked for\n" ),
                                                  m_name ),
                                RPT_SEVERITY_WARNING );
    }

private:
    std::vector<IMPORT_PROJECT_DESC> choose( const std::vector<IMPORT_PROJECT_DESC>& aProjects )
    {
        m_asked = true;

        if( m_name.empty() )
            return ChooseFirstProject( m_reporter )( aProjects );

        std::vector<size_t> matches;

        for( size_t i = 0; i < aProjects.size(); ++i )
        {
            if( DescribeProject( aProjects[i] ) == m_name )
                return chosen( aProjects, i );

            if( ProjectName( aProjects[i] ) == m_name )
                matches.push_back( i );
        }

        if( matches.size() == 1 )
            return chosen( aProjects, matches[0] );

        m_failed = true;

        if( matches.empty() )
        {
            m_reporter->Report( wxString::Format( _( "The source holds no project %s; it holds %s\n" ),
                                                  m_name, DescribeProjects( aProjects ) ),
                                RPT_SEVERITY_ERROR );
        }
        else
        {
            std::vector<IMPORT_PROJECT_DESC> named;

            for( size_t i : matches )
                named.push_back( aProjects[i] );

            m_reporter->Report( wxString::Format( _( "The source holds several projects named '%s' "
                                                     "(%s); name one as listed\n" ),
                                                  m_name, DescribeProjects( named ) ),
                                RPT_SEVERITY_ERROR );
        }

        return {};
    }

    std::vector<IMPORT_PROJECT_DESC> chosen( const std::vector<IMPORT_PROJECT_DESC>& aProjects,
                                             size_t aIndex )
    {
        ReportProjectChosen( m_reporter, aProjects, aIndex );
        return { aProjects[aIndex] };
    }

    wxString  m_name;
    REPORTER* m_reporter;
    bool      m_asked = false;
    bool      m_failed = false;
};


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

    /**
     * @brief The projects \a aFileName holds, as the handler would be offered them, read without
     * loading the file: all of them, even when there is only one, which the handler isn't asked
     * about. None when the plugin can't tell.
     */
    virtual std::vector<IMPORT_PROJECT_DESC> GetProjects( const wxString& aFileName ) const
    {
        return {};
    }

    virtual ~PROJECT_CHOOSER_PLUGIN() = default;

protected:
    /// Callback to choose projects to import; the first one until a caller registers another
    CHOOSE_PROJECT_HANDLER m_choose_project_handler = ChooseFirstProject();
};

#endif // PLUGIN_COMMON_CHOOSE_PROJECT_H
